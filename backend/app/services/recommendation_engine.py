"""Zero-hardcode 7-stage Best Time & Days to Post Recommendation Engine."""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.entities import (
    AudienceSnapshot,
    BrandProfile,
    InstagramAccount,
    MediaItem,
    NichePriorCache,
    ScheduledPost,
)
from app.services.gemini_helper import generate_structured_content

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
EPS = 1e-9


class DynamicNichePriorSchema(BaseModel):
    """Structured 7x24 audience engagement prior generated dynamically for a niche+timezone."""

    hourly_weights_7x24: list[list[float]] = Field(
        description="7 rows (Monday=0 .. Sunday=6), each with 24 positive floats (hour 0..23) representing relative audience receptivity around 1.0"
    )
    rationale: str = Field(
        description="Brief explanation of niche & timezone audience patterns"
    )


@dataclass
class RecommendationConfig:
    likes_w: float = 1.0
    comments_w: float = 2.5
    shares_w: float = 4.0
    saves_w: float = 4.0
    clicks_w: float = 2.0
    half_life_days: float = 30.0
    bayesian_m: float = 3.0
    lookback_days: int = 90
    platform_min_gap_hours: int = 12
    min_lead_minutes: int = 30
    ucb_learning_c: float = 0.15
    ucb_calibrating_c: float = 0.08
    ucb_confident_c: float = 0.04

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "RecommendationConfig":
        s = settings or get_settings()
        return cls(
            likes_w=s.rec_likes_weight,
            comments_w=s.rec_comments_weight,
            shares_w=s.rec_shares_weight,
            saves_w=s.rec_saves_weight,
            clicks_w=s.rec_clicks_weight,
            half_life_days=s.rec_half_life_days,
            bayesian_m=s.rec_bayesian_m,
            lookback_days=s.rec_lookback_days,
            platform_min_gap_hours=s.rec_platform_min_gap_hours,
            min_lead_minutes=s.rec_min_lead_minutes,
            ucb_learning_c=s.rec_ucb_learning_c,
            ucb_calibrating_c=s.rec_ucb_calibrating_c,
            ucb_confident_c=s.rec_ucb_confident_c,
        )


@dataclass
class PostTelemetry:
    post_id: str
    published_at_utc: datetime
    content_format: str
    reach: int
    impressions: int
    likes: int
    comments: int
    shares: int
    saves: int
    clicks: int = 0
    followers_fallback: int = 1


@dataclass
class RecommendedSlot:
    datetime_utc: datetime
    datetime_local: datetime
    local_display: str
    day_of_week: int
    day_name: str
    hour_of_day: int
    time_label: str
    score: float
    display_score_100: float
    slot_type: Literal["proven", "benchmark", "exploration"]
    sample_count: int
    n_eff: float
    lift_vs_baseline: float
    reason: str


@dataclass
class RecommendationResult:
    confidence_tier: Literal["learning", "calibrating", "high_confidence"]
    posts_analyzed_count: int
    lookback_days: int
    min_gap_hours: int
    timezone: str
    next_recommended_slot: RecommendedSlot | None
    weekly_schedule: list[RecommendedSlot]
    avoid_windows: list[dict[str, Any]]
    heatmap: list[dict[str, Any]]
    prior_source: str


def _safe_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        return ZoneInfo("UTC")


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_dynamic_min_gap_hours(
    target_posts_per_week: int, platform_min_gap_hours: int
) -> int:
    weekly_target = max(1, target_posts_per_week)
    computed = math.floor((168.0 / weekly_target) * 0.65)
    return max(platform_min_gap_hours, computed)


def _normalize_grid_to_mean_one(grid: list[list[float]]) -> list[list[float]]:
    flat = [max(0.0, float(val)) for row in grid for val in row]
    mean_val = sum(flat) / max(len(flat), 1)
    if mean_val <= EPS:
        return [[1.0 for _ in range(24)] for _ in range(7)]
    return [[max(0.1, float(grid[d][h]) / mean_val) for h in range(24)] for d in range(7)]


def _shift_grid_pre_peak(grid: list[list[float]], shift_hours: int = 1) -> list[list[float]]:
    """Shift audience online curve earlier by `shift_hours` (30-60 min pre-peak velocity)."""
    shifted: list[list[float]] = []
    for d in range(7):
        row = []
        for h in range(24):
            # Placing post at hour `h` captures peak traffic arriving at `(h + shift_hours) % 24`
            target_h = (h + shift_hours) % 24
            target_d = (d + (1 if h + shift_hours >= 24 else 0)) % 7
            row.append(float(grid[target_d][target_h]))
        shifted.append(row)
    return shifted


def _expand_online_followers(raw_map: dict[str, Any]) -> list[list[float]] | None:
    if not raw_map:
        return None
    hourly = [0.0] * 24
    has_val = False
    for k, v in raw_map.items():
        if str(k).isdigit():
            h = int(k)
            if 0 <= h < 24:
                hourly[h] = max(0.0, float(v))
                if hourly[h] > 0:
                    has_val = True
    if not has_val:
        return None
    grid_7x24 = [list(hourly) for _ in range(7)]
    return _normalize_grid_to_mean_one(_shift_grid_pre_peak(grid_7x24, shift_hours=1))


def run_7stage_recommendation(
    *,
    posts: list[PostTelemetry],
    timezone_name: str,
    target_posts_per_week: int = 4,
    now_utc: datetime | None = None,
    follower_online_grid: list[list[float]] | None = None,
    niche_prior_grid: list[list[float]] | None = None,
    prior_source: str = "uniform",
    last_published_at_utc: datetime | None = None,
    existing_queued_times_utc: list[datetime] | None = None,
    content_format_filter: str | None = None,
    config: RecommendationConfig | None = None,
    schedule_count: int | None = None,
    horizon_days: int = 7,
) -> RecommendationResult:
    """Execute the complete 7-stage Zero-Hardcode Best-Time Recommendation pipeline."""
    cfg = config or RecommendationConfig()
    tz = _safe_tz(timezone_name)
    now = _as_utc(now_utc or datetime.now(timezone.utc))
    lookback_cutoff = now - timedelta(days=cfg.lookback_days)

    # Filter posts inside lookback window
    recent_posts = [p for p in posts if _as_utc(p.published_at_utc) >= lookback_cutoff]
    if content_format_filter:
        fmt_matches = [
            p for p in recent_posts if p.content_format.lower() == content_format_filter.lower()
        ]
        if len(fmt_matches) >= 3:
            recent_posts = fmt_matches

    n_total = len(recent_posts)

    # Stage 2: Weighted Engagement Score
    raw_scores: list[tuple[PostTelemetry, float]] = []
    scores_by_format: dict[str, list[float]] = {}
    for p in recent_posts:
        exposure = p.reach if p.reach > 0 else (p.impressions if p.impressions > 0 else max(p.followers_fallback, 1))
        weighted_eng = (
            cfg.likes_w * p.likes
            + cfg.comments_w * p.comments
            + cfg.shares_w * p.shares
            + cfg.saves_w * p.saves
            + cfg.clicks_w * p.clicks
        )
        post_score = weighted_eng / max(float(exposure), 1.0)
        raw_scores.append((p, post_score))
        scores_by_format.setdefault(p.content_format.lower(), []).append(post_score)

    # Stage 3: Content-Format Normalization (De-biasing)
    all_raw = [s for _, s in raw_scores]
    global_median = median(all_raw) if all_raw else 1.0
    format_medians: dict[str, float] = {}
    for fmt, vals in scores_by_format.items():
        format_medians[fmt] = median(vals) if len(vals) >= 3 else global_median

    norm_entries: list[tuple[PostTelemetry, float, float]] = []
    for p, post_score in raw_scores:
        fmt_med = format_medians.get(p.content_format.lower(), global_median)
        norm_score = post_score / max(fmt_med, EPS) if global_median > EPS else 1.0
        age_days = max(0.0, (now - _as_utc(p.published_at_utc)).total_seconds() / 86400.0)
        decay_lambda = math.log(2.0) / max(cfg.half_life_days, 1.0)
        decay_weight = math.exp(-decay_lambda * age_days)
        norm_entries.append((p, norm_score, decay_weight))

    # Stage 4: Outlier Protection (MAD / 95th Percentile Winsorization)
    if len(norm_entries) >= 5:
        sorted_norms = sorted(item[1] for item in norm_entries)
        p95_idx = min(len(sorted_norms) - 1, math.floor(0.95 * (len(sorted_norms) - 1)))
        p95_val = sorted_norms[p95_idx]
        med_norm = median(sorted_norms)
        abs_devs = [abs(x - med_norm) for x in sorted_norms]
        mad = median(abs_devs) * 1.4826
        mad_cap = med_norm + 3.0 * mad if mad > EPS else p95_val
        outlier_cap = max(min(p95_val, mad_cap), med_norm * 1.25)
        norm_entries = [
            (p, min(norm_s, outlier_cap), w) for p, norm_s, w in norm_entries
        ]

    # Stage 1: Timezone Alignment & Circular Kernel Smoothing (0.2 / 0.6 / 0.2)
    weighted_sum = [[0.0 for _ in range(24)] for _ in range(7)]
    n_eff = [[0.0 for _ in range(24)] for _ in range(7)]
    direct_counts = [[0 for _ in range(24)] for _ in range(7)]

    for p, norm_score, decay_w in norm_entries:
        local_dt = _as_utc(p.published_at_utc).astimezone(tz)
        dow = local_dt.weekday()
        hour = local_dt.hour
        direct_counts[dow][hour] += 1

        kernel = [(-1, 0.2), (0, 0.6), (1, 0.2)]
        for offset_h, k_weight in kernel:
            target_h = (hour + offset_h) % 24
            if hour + offset_h < 0:
                target_d = (dow - 1) % 7
            elif hour + offset_h >= 24:
                target_d = (dow + 1) % 7
            else:
                target_d = dow
            eff_w = decay_w * k_weight
            n_eff[target_d][target_h] += eff_w
            weighted_sum[target_d][target_h] += eff_w * norm_score

    # Stage 5: Dynamic Cold-Start Prior + Bayesian Posterior
    if follower_online_grid and niche_prior_grid:
        prior_grid = [
            [
                0.65 * follower_online_grid[d][h] + 0.35 * niche_prior_grid[d][h]
                for h in range(24)
            ]
            for d in range(7)
        ]
        prior_grid = _normalize_grid_to_mean_one(prior_grid)
        resolved_prior_source = "follower_online+dynamic_niche"
    elif follower_online_grid:
        prior_grid = _normalize_grid_to_mean_one(follower_online_grid)
        resolved_prior_source = "follower_online_prepeak"
    elif niche_prior_grid:
        prior_grid = _normalize_grid_to_mean_one(niche_prior_grid)
        resolved_prior_source = "dynamic_niche_prior"
    elif norm_entries:
        total_w = sum(item[2] for item in norm_entries)
        global_w_mean = (
            sum(item[1] * item[2] for item in norm_entries) / max(total_w, EPS)
        )
        prior_grid = [[global_w_mean for _ in range(24)] for _ in range(7)]
        resolved_prior_source = "account_global_mean"
    else:
        prior_grid = [[1.0 for _ in range(24)] for _ in range(7)]
        resolved_prior_source = prior_source

    prior_flat = [prior_grid[d][h] for d in range(7) for h in range(24)]
    prior_mean = sum(prior_flat) / max(len(prior_flat), 1)

    posterior_grid = [[0.0 for _ in range(24)] for _ in range(7)]
    for d in range(7):
        for h in range(24):
            eff = n_eff[d][h]
            x_bar = weighted_sum[d][h] / max(eff, EPS) if eff > 0 else prior_grid[d][h]
            posterior_grid[d][h] = (eff * x_bar + cfg.bayesian_m * prior_grid[d][h]) / (
                eff + cfg.bayesian_m
            )

    # Stage 6: Adaptive Confidence & UCB Exploration Bonus
    if n_total < 15:
        confidence_tier: Literal["learning", "calibrating", "high_confidence"] = "learning"
        ucb_c = cfg.ucb_learning_c
    elif n_total < 40:
        confidence_tier = "calibrating"
        ucb_c = cfg.ucb_calibrating_c
    else:
        confidence_tier = "high_confidence"
        ucb_c = cfg.ucb_confident_c

    final_grid = [[0.0 for _ in range(24)] for _ in range(7)]
    slot_types: list[list[Literal["proven", "benchmark", "exploration"]]] = [
        ["exploration" for _ in range(24)] for _ in range(7)
    ]

    for d in range(7):
        for h in range(24):
            eff = n_eff[d][h]
            ucb_bonus = ucb_c * math.sqrt(math.log(n_total + 2.0) / (eff + 1.0))
            final_grid[d][h] = posterior_grid[d][h] + ucb_bonus
            if direct_counts[d][h] >= 2 or eff >= 1.5:
                slot_types[d][h] = "proven"
            elif prior_grid[d][h] > prior_mean * 1.05:
                slot_types[d][h] = "benchmark"
            else:
                slot_types[d][h] = "exploration"

    all_final = [final_grid[d][h] for d in range(7) for h in range(24)]
    min_f, max_f = min(all_final), max(all_final)
    baseline_posterior = sum(
        posterior_grid[d][h] for d in range(7) for h in range(24)
    ) / 168.0

    def _to_score_100(val: float) -> float:
        if max_f - min_f <= EPS:
            # Uniform flat scores -> scale around 50.0 instead of misleading 100.0
            return 50.0 if n_total == 0 else round(min(100.0, val * 50.0), 1)
        return round(((val - min_f) / (max_f - min_f)) * 100.0, 1)

    heatmap: list[dict[str, Any]] = []
    for d in range(7):
        for h in range(24):
            post_val = posterior_grid[d][h]
            lift_pct = round(((post_val / max(baseline_posterior, EPS)) - 1.0) * 100.0, 1)
            heatmap.append(
                {
                    "day_of_week": d,
                    "day_name": DAY_NAMES[d],
                    "hour_of_day": h,
                    "combined_score": _to_score_100(final_grid[d][h]),
                    "raw_final_score": round(final_grid[d][h], 4),
                    "posterior_score": round(post_val, 4),
                    "prior_score": round(prior_grid[d][h], 4),
                    "reach_score": round(post_val * 100.0, 2),
                    "audience_online_score": round(
                        (follower_online_grid[d][h] * 100.0) if follower_online_grid else 0.0, 2
                    ),
                    "sample_size": direct_counts[d][h],
                    "n_eff": round(n_eff[d][h], 2),
                    "slot_type": slot_types[d][h],
                    "lift_vs_baseline": lift_pct,
                    "confidence": round(
                        min(
                            1.0,
                            (
                                (max(n_eff[d][h], direct_counts[d][h] * 0.65) / 3.0)
                                * (0.7 if follower_online_grid else 1.0)
                                + (0.3 if follower_online_grid else 0.0)
                            )
                            if (n_eff[d][h] > 0 or follower_online_grid)
                            else 0.0,
                        ),
                        2,
                    ),
                }
            )

    # Stage 7: Anti-Cannibalization Spacing (Greedy NMS)
    min_gap_hours = compute_dynamic_min_gap_hours(
        target_posts_per_week, cfg.platform_min_gap_hours
    )
    desired_slots = schedule_count if schedule_count is not None else max(1, target_posts_per_week)

    occupied: list[datetime] = []
    if last_published_at_utc is not None:
        occupied.append(_as_utc(last_published_at_utc))
    for q_dt in existing_queued_times_utc or []:
        occupied.append(_as_utc(q_dt))

    earliest_utc = now + timedelta(minutes=cfg.min_lead_minutes)
    start_local = earliest_utc.astimezone(tz).replace(minute=0, second=0, microsecond=0) + timedelta(
        hours=1
    )

    effective_horizon = max(horizon_days, math.ceil((desired_slots * min_gap_hours) / 24.0) + 3)
    candidates: list[tuple[float, datetime, datetime, int, int]] = []
    for offset_h in range(effective_horizon * 24):
        cand_local = start_local + timedelta(hours=offset_h)
        cand_utc = cand_local.astimezone(timezone.utc)
        if cand_utc < earliest_utc:
            continue
        d = cand_local.weekday()
        h = cand_local.hour
        # Mild tie-breaking decay across days so equally high windows don't all pile on day 7
        rank_score = final_grid[d][h] - (offset_h / 24.0) * 0.005
        candidates.append((rank_score, cand_utc, cand_local, d, h))

    candidates.sort(key=lambda item: item[0], reverse=True)

    selected: list[RecommendedSlot] = []
    for _, cand_utc, cand_local, d, h in candidates:
        if len(selected) >= desired_slots:
            break
        if any(abs((cand_utc - occ).total_seconds()) < min_gap_hours * 3600 for occ in occupied):
            continue

        occupied.append(cand_utc)
        post_val = posterior_grid[d][h]
        lift_pct = round(((post_val / max(baseline_posterior, EPS)) - 1.0) * 100.0, 1)
        s_type = slot_types[d][h]
        score_100 = _to_score_100(final_grid[d][h])
        time_lbl = cand_local.strftime("%I:%M %p").lstrip("0")
        local_disp = cand_local.strftime("%a, %b %d at %I:%M %p")

        if s_type == "proven":
            reason = (
                f"{DAY_NAMES[d]} {time_lbl} ({timezone_name}) is a Proven peak slot: "
                f"{direct_counts[d][h]} historical post(s) (n_eff={n_eff[d][h]:.1f}) delivered "
                f"{lift_pct:+.1f}% vs your account baseline, spaced {min_gap_hours}h from nearby posts."
            )
        elif s_type == "benchmark":
            reason = (
                f"{DAY_NAMES[d]} {time_lbl} ({timezone_name}) is a Benchmark high-receptivity slot "
                f"({resolved_prior_source}, {lift_pct:+.1f}% prior lift), spaced {min_gap_hours}h from nearby posts."
            )
        else:
            reason = (
                f"{DAY_NAMES[d]} {time_lbl} ({timezone_name}) is an Exploration / Test Slot "
                f"(UCB {confidence_tier} mode with {n_total} analyzed posts) to discover fresh reach windows, "
                f"spaced {min_gap_hours}h apart."
            )

        selected.append(
            RecommendedSlot(
                datetime_utc=cand_utc,
                datetime_local=cand_local,
                local_display=local_disp,
                day_of_week=d,
                day_name=DAY_NAMES[d],
                hour_of_day=h,
                time_label=time_lbl,
                score=round(final_grid[d][h], 4),
                display_score_100=score_100,
                slot_type=s_type,
                sample_count=direct_counts[d][h],
                n_eff=round(n_eff[d][h], 2),
                lift_vs_baseline=lift_pct,
                reason=reason,
            )
        )

    selected.sort(key=lambda item: item.datetime_utc)

    # Identify avoid_windows (lowest posterior scores with meaningful history or lowest prior)
    avoid_candidates = [
        cell
        for cell in heatmap
        if (cell["sample_size"] >= 1 or cell["prior_score"] < prior_mean * 0.92)
    ]
    if not avoid_candidates:
        avoid_candidates = sorted(heatmap, key=lambda c: c["posterior_score"])[:5]
    else:
        avoid_candidates.sort(key=lambda c: (c["posterior_score"], -c["sample_size"]))

    avoid_windows = [
        {
            "day_of_week": c["day_of_week"],
            "day_name": c["day_name"],
            "hour_of_day": c["hour_of_day"],
            "label": f"{c['day_name']} {c['hour_of_day']:02d}:00",
            "lift_vs_baseline": c["lift_vs_baseline"],
            "sample_size": c["sample_size"],
            "reason": (
                f"Underperformed by {abs(c['lift_vs_baseline']):.1f}% vs baseline across {c['sample_size']} post(s)"
                if c["sample_size"] > 0
                else f"Low prior audience activity ({c['lift_vs_baseline']:+.1f}% vs baseline)"
            ),
        }
        for c in avoid_candidates[:5]
    ]

    return RecommendationResult(
        confidence_tier=confidence_tier,
        posts_analyzed_count=n_total,
        lookback_days=cfg.lookback_days,
        min_gap_hours=min_gap_hours,
        timezone=timezone_name,
        next_recommended_slot=selected[0] if selected else None,
        weekly_schedule=selected,
        avoid_windows=avoid_windows,
        heatmap=heatmap,
        prior_source=resolved_prior_source,
    )


async def get_or_compute_dynamic_niche_prior(
    db: AsyncSession,
    *,
    niche: str,
    timezone_name: str,
    settings: Settings | None = None,
) -> tuple[list[list[float]] | None, str]:
    """Fetch or dynamically compute & cache a 7x24 niche-timezone prior via Gemini (zero hardcoding)."""
    cfg = settings or get_settings()
    clean_niche = (niche or "general creator & business").strip().lower()[:180]
    tz = timezone_name or "UTC"
    now = datetime.now(timezone.utc)

    cached = (
        await db.execute(
            select(NichePriorCache).where(
                NichePriorCache.niche_key == clean_niche,
                NichePriorCache.timezone == tz,
            )
        )
    ).scalar_one_or_none()

    if cached and cached.prior_grid_json:
        exp = _as_utc(cached.expires_at)
        if exp > now and len(cached.prior_grid_json) == 7:
            return cached.prior_grid_json, cached.rationale

    if not cfg.gemini_api_key:
        return None, ""

    prompt = (
        "Estimate a realistic 7x24 Instagram audience engagement receptivity prior grid "
        f"for niche='{clean_niche}' in local timezone='{tz}'.\n"
        "Return `hourly_weights_7x24` as 7 rows (Monday=0 .. Sunday=6), each with exactly 24 floats "
        "between 0.3 and 1.8 (where 1.0 is average, >1.2 are peak waking/commute/evening windows for this niche & timezone, "
        "and 0.3-0.5 are overnight sleeping hours)."
    )
    try:
        generated = await generate_structured_content(
            cfg,
            prompt=prompt,
            response_schema=DynamicNichePriorSchema,
            temperature=0.25,
        )
        raw_grid = generated.hourly_weights_7x24
        if len(raw_grid) == 7 and all(len(row) == 24 for row in raw_grid):
            norm_grid = _normalize_grid_to_mean_one(raw_grid)
            if cached is None:
                cached = NichePriorCache(niche_key=clean_niche, timezone=tz)
                db.add(cached)
            cached.prior_grid_json = norm_grid
            cached.rationale = generated.rationale
            cached.computed_at = now
            cached.expires_at = now + timedelta(days=14)
            await db.commit()
            return norm_grid, generated.rationale
    except Exception:
        pass
    return None, ""


async def build_account_recommendations(
    db: AsyncSession,
    account: InstagramAccount,
    brand: BrandProfile | None = None,
    *,
    content_format_filter: str | None = None,
    schedule_count: int | None = None,
    earliest_start: datetime | None = None,
    min_gap_hours_override: int | None = None,
    allow_dynamic_niche_prior: bool = True,
    settings: Settings | None = None,
) -> RecommendationResult:
    """Load account telemetry from DB and execute the 7-stage recommendation engine."""
    cfg_settings = settings or get_settings()
    rec_cfg = RecommendationConfig.from_settings(cfg_settings)
    if min_gap_hours_override is not None:
        rec_cfg.platform_min_gap_hours = min_gap_hours_override

    media_rows = (
        await db.execute(select(MediaItem).where(MediaItem.account_id == account.id))
    ).scalars().all()

    telemetry = [
        PostTelemetry(
            post_id=m.ig_media_id,
            published_at_utc=m.published_at,
            content_format=m.media_type,
            reach=m.reach,
            impressions=m.views,
            likes=m.likes,
            comments=m.comments,
            shares=m.shares,
            saves=m.saves,
            followers_fallback=max(account.followers_count, 1),
        )
        for m in media_rows
    ]

    latest_snap = (
        await db.execute(
            select(AudienceSnapshot)
            .where(AudienceSnapshot.account_id == account.id)
            .order_by(AudienceSnapshot.snapshot_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    follower_online_grid = (
        _expand_online_followers(latest_snap.online_followers_hourly_json)
        if latest_snap
        else None
    )

    niche_prior_grid: list[list[float]] | None = None
    if allow_dynamic_niche_prior and len(telemetry) < 40:
        niche_text = (brand.niche if brand and brand.niche else account.biography) or "instagram creator"
        niche_prior_grid, _ = await get_or_compute_dynamic_niche_prior(
            db, niche=niche_text, timezone_name=account.timezone, settings=cfg_settings
        )

    scheduled_rows = (
        await db.execute(
            select(ScheduledPost).where(ScheduledPost.account_id == account.id)
        )
    ).scalars().all()

    queued_times = [
        p.scheduled_for for p in scheduled_rows if p.status in ("QUEUED", "PUBLISHING")
    ]
    published_dates = [
        _as_utc(m.published_at) for m in media_rows
    ] + [
        _as_utc(p.published_at)
        for p in scheduled_rows
        if p.status == "PUBLISHED" and p.published_at is not None
    ]
    last_published = max(published_dates) if published_dates else None

    target_weekly = brand.posting_goal_per_week if brand and brand.posting_goal_per_week > 0 else 4
    effective_now = earliest_start if earliest_start is not None else datetime.now(timezone.utc)

    return run_7stage_recommendation(
        posts=telemetry,
        timezone_name=account.timezone,
        target_posts_per_week=target_weekly,
        now_utc=effective_now,
        follower_online_grid=follower_online_grid,
        niche_prior_grid=niche_prior_grid,
        last_published_at_utc=last_published,
        existing_queued_times_utc=queued_times,
        content_format_filter=content_format_filter,
        config=rec_cfg,
        schedule_count=schedule_count,
    )


def serialize_recommendation_result(res: RecommendationResult) -> dict[str, Any]:
    def _slot_dict(s: RecommendedSlot) -> dict[str, Any]:
        return {
            "datetime_utc": s.datetime_utc.isoformat(),
            "datetime_local": s.datetime_local.isoformat(),
            "local_display": s.local_display,
            "day_of_week": s.day_of_week,
            "day_name": s.day_name,
            "hour_of_day": s.hour_of_day,
            "time_label": s.time_label,
            "score": s.score,
            "display_score_100": s.display_score_100,
            "slot_type": s.slot_type,
            "sample_count": s.sample_count,
            "n_eff": s.n_eff,
            "lift_vs_baseline": s.lift_vs_baseline,
            "reason": s.reason,
        }

    return {
        "confidence_tier": res.confidence_tier,
        "posts_analyzed_count": res.posts_analyzed_count,
        "lookback_days": res.lookback_days,
        "min_gap_hours": res.min_gap_hours,
        "timezone": res.timezone,
        "prior_source": res.prior_source,
        "next_recommended_slot": (
            _slot_dict(res.next_recommended_slot) if res.next_recommended_slot else None
        ),
        "weekly_schedule": [_slot_dict(s) for s in res.weekly_schedule],
        "avoid_windows": res.avoid_windows,
        "heatmap": res.heatmap,
    }

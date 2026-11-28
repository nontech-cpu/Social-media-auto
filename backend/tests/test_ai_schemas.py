"""Validate every Gemini response_schema against google-genai's schema transformer."""

from types import SimpleNamespace

from google.genai import _transformers as genai_transformers

from app.services.ai_copilot import (
    AccountAuditOutput,
    BrandEnrichmentOutput,
    CopilotChatAction,
    GeneratedPostPackage,
)
from app.services.google_business_service import ReviewReplyOutput
from app.services.hashtag_engine import HashtagDiscoveryResult
from app.services.recommendation_engine import DynamicNichePriorSchema


def test_all_gemini_response_schemas_are_valid() -> None:
    dummy_client = SimpleNamespace(vertexai=False)
    schemas = [
        BrandEnrichmentOutput,
        AccountAuditOutput,
        GeneratedPostPackage,
        CopilotChatAction,
        ReviewReplyOutput,
        HashtagDiscoveryResult,
        DynamicNichePriorSchema,
    ]
    for schema in schemas:
        transformed = genai_transformers.t_schema(dummy_client, schema)
        assert transformed is not None

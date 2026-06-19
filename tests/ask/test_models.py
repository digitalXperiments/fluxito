from app.models.ai_catalog import AiCatalogModel
from app.models.conversation import AIProviderKey, ChatMessage, Conversation


def test_tables_named():
    assert Conversation.__tablename__ == "conversations"
    assert ChatMessage.__tablename__ == "chat_messages"
    assert AIProviderKey.__tablename__ == "ai_provider_keys"
    assert AiCatalogModel.__tablename__ == "ai_catalog_models"


def test_chat_message_has_jsonb_content_and_seq():
    cols = ChatMessage.__table__.columns
    assert "content" in cols and "seq" in cols and "role" in cols
    assert "conversation_id" in cols


def test_catalog_model_has_required_columns():
    cols = AiCatalogModel.__table__.columns
    assert "provider" in cols
    assert "model_id" in cols
    assert "source" in cols
    assert "is_enabled" in cols
    assert "is_deprecated" in cols
    assert "capabilities" in cols

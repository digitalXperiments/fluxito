from app.models.conversation import AIProviderKey, ChatMessage, Conversation


def test_tables_named():
    assert Conversation.__tablename__ == "conversations"
    assert ChatMessage.__tablename__ == "chat_messages"
    assert AIProviderKey.__tablename__ == "ai_provider_keys"


def test_chat_message_has_jsonb_content_and_seq():
    cols = ChatMessage.__table__.columns
    assert "content" in cols and "seq" in cols and "role" in cols
    assert "conversation_id" in cols

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, DateTime,
    ForeignKey, Numeric, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    pass


def new_uuid():
    return str(uuid.uuid4())


class Customer(Base):
    __tablename__ = "customers"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    metadata_ = Column("metadata", JSONB, default=dict)

    orders = relationship("Order", back_populates="customer")
    conversations = relationship("Conversation", back_populates="customer")
    refunds = relationship("Refund", back_populates="customer")


class Product(Base):
    __tablename__ = "products"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)  # electronics, clothing, home_goods, accessories
    price = Column(Numeric(10, 2), nullable=False)
    return_window_days = Column(Integer, nullable=False, default=30)
    warranty_months = Column(Integer, nullable=True)
    final_sale = Column(Boolean, nullable=False, default=False)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    order_items = relationship("OrderItem", back_populates="product")


class Order(Base):
    __tablename__ = "orders"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    customer_id = Column(UUID(as_uuid=False), ForeignKey("customers.id"), nullable=False)
    status = Column(String, nullable=False)  # placed, processing, shipped, delivered, return_in_progress, returned, cancelled, refunded
    total_amount = Column(Numeric(10, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    customer = relationship("Customer", back_populates="orders")
    items = relationship("OrderItem", back_populates="order")
    refunds = relationship("Refund", back_populates="order")

    __table_args__ = (
        Index("idx_orders_customer", "customer_id"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    order_id = Column(UUID(as_uuid=False), ForeignKey("orders.id"), nullable=False)
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    price_at_purchase = Column(Numeric(10, 2), nullable=False)

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")


class Refund(Base):
    __tablename__ = "refunds"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    order_id = Column(UUID(as_uuid=False), ForeignKey("orders.id"), nullable=False)
    customer_id = Column(UUID(as_uuid=False), ForeignKey("customers.id"), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    reason = Column(String, nullable=False)  # defective, changed_mind, wrong_item, late_delivery, other
    status = Column(String, nullable=False, default="requested")  # requested, approved, rejected, processed
    initiated_by = Column(String, nullable=False, default="customer")  # customer, agent, system
    conversation_id = Column(UUID(as_uuid=False), ForeignKey("conversations.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)

    order = relationship("Order", back_populates="refunds")
    customer = relationship("Customer", back_populates="refunds")

    __table_args__ = (
        Index("idx_refunds_customer", "customer_id"),
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    customer_id = Column(UUID(as_uuid=False), ForeignKey("customers.id"), nullable=False)
    status = Column(String, nullable=False, default="active")  # active, resolved, escalated
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    summary = Column(Text, nullable=True)
    turn_state = Column(JSONB, nullable=True)
    messages_purged = Column(Boolean, default=False)
    csat_score = Column(Integer, nullable=True)  # 1-5
    csat_comment = Column(Text, nullable=True)

    customer = relationship("Customer", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation")
    audit_logs = relationship("AuditLog", back_populates="conversation")
    escalations = relationship("Escalation", back_populates="conversation")

    __table_args__ = (
        Index("idx_conversations_customer", "customer_id"),
        Index("idx_conversations_status", "status"),
        Index(
            "idx_conversations_csat",
            "csat_score",
            postgresql_where="csat_score IS NOT NULL",
        ),
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    conversation_id = Column(UUID(as_uuid=False), ForeignKey("conversations.id"), nullable=False)
    role = Column(String, nullable=False)  # customer, agent, system
    content = Column(Text, nullable=False)
    agent_type = Column(String, nullable=True)  # supervisor, knowledge, action, escalation
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    conversation = relationship("Conversation", back_populates="messages")
    audit_logs = relationship("AuditLog", back_populates="message")

    __table_args__ = (
        Index("idx_messages_conversation", "conversation_id"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    conversation_id = Column(UUID(as_uuid=False), ForeignKey("conversations.id"), nullable=False)
    message_id = Column(UUID(as_uuid=False), ForeignKey("messages.id"), nullable=True)
    agent_type = Column(String, nullable=False)
    action = Column(String, nullable=False)
    input_data = Column(JSONB, default=dict)
    output_data = Column(JSONB, default=dict)
    routing_decision = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    conversation = relationship("Conversation", back_populates="audit_logs")
    message = relationship("Message", back_populates="audit_logs")

    __table_args__ = (
        Index("idx_audit_conversation", "conversation_id"),
        Index("idx_audit_message", "message_id"),
    )


class Escalation(Base):
    __tablename__ = "escalations"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    conversation_id = Column(UUID(as_uuid=False), ForeignKey("conversations.id"), nullable=False)
    reason = Column(String, nullable=False)  # customer_requested, low_confidence, unknown_intent, policy_exception, repeated_failure
    agent_confidence = Column(Float, nullable=True)
    context_summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    conversation = relationship("Conversation", back_populates="escalations")

    __table_args__ = (
        Index("idx_escalations_conversation", "conversation_id"),
        Index("idx_escalations_reason", "reason"),
    )


class KBDocument(Base):
    __tablename__ = "kb_documents"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    filename = Column(String, nullable=False)
    title = Column(String, nullable=False)
    category = Column(String, nullable=False)  # returns, shipping, payments, products, account, warranty
    version = Column(Integer, default=1)
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())
    metadata_ = Column("metadata", JSONB, default=dict)

    chunks = relationship("KBChunk", back_populates="document", cascade="all, delete-orphan")


class KBChunk(Base):
    __tablename__ = "kb_chunks"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    document_id = Column(UUID(as_uuid=False), ForeignKey("kb_documents.id"), nullable=False)
    chunk_text = Column(Text, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    embedding = Column(Vector(1536), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    document = relationship("KBDocument", back_populates="chunks")

    __table_args__ = (
        Index("idx_kb_chunks_document", "document_id"),
    )

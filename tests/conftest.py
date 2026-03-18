"""
Shared test fixtures.

Requires a test PostgreSQL database with pgvector:
    createdb support_agent_test

Override the URL with:
    TEST_DATABASE_URL=postgresql+asyncpg://... pytest
"""
import asyncio
import os
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from backend.main import app
from backend.db.models import Base, Customer, Product, Order, OrderItem, Conversation, Message
from backend.db.session import get_db

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://jad@localhost:5432/support_agent_test",
)


# ---------------------------------------------------------------------------
# Schema setup — plain sync fixture using asyncio.run().
# Runs once per session without touching pytest-asyncio's event loop at all,
# which eliminates all cross-loop conflicts on Python 3.9.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def setup_database():
    async def _create():
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    async def _drop():
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(_create())
    yield
    asyncio.run(_drop())


# ---------------------------------------------------------------------------
# DB session — fresh engine + session per test (NullPool = no shared state).
# Teardown: rollback any uncommitted work, then truncate committed rows.
# ---------------------------------------------------------------------------

@pytest.fixture
async def db(setup_database) -> AsyncSession:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
        await session.rollback()
    # Truncate all tables to clean up anything committed by route handlers
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    await engine.dispose()


# ---------------------------------------------------------------------------
# HTTP client — injects the test session into FastAPI's get_db dependency
# ---------------------------------------------------------------------------

@pytest.fixture
async def client(db) -> AsyncClient:
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Data factories
# ---------------------------------------------------------------------------

@pytest.fixture
async def customer(db) -> Customer:
    c = Customer(
        id=str(uuid.uuid4()),
        name="Test Customer",
        email=f"test_{uuid.uuid4().hex[:8]}@example.com",
        metadata_={},
    )
    db.add(c)
    await db.flush()
    return c


@pytest.fixture
async def product(db) -> Product:
    p = Product(
        id=str(uuid.uuid4()),
        name="Test Laptop",
        category="electronics",
        price=999.99,
        return_window_days=14,
        warranty_months=12,
        metadata_={},
    )
    db.add(p)
    await db.flush()
    return p


@pytest.fixture
async def order(db, customer, product) -> Order:
    o = Order(
        id=str(uuid.uuid4()),
        customer_id=customer.id,
        status="placed",
        total_amount=999.99,
    )
    db.add(o)
    await db.flush()
    db.add(OrderItem(
        id=str(uuid.uuid4()),
        order_id=o.id,
        product_id=product.id,
        quantity=1,
        price_at_purchase=999.99,
    ))
    await db.flush()
    return o


@pytest.fixture
async def active_conversation(db, customer) -> Conversation:
    c = Conversation(
        id=str(uuid.uuid4()),
        customer_id=customer.id,
        status="active",
    )
    db.add(c)
    await db.flush()
    return c


@pytest.fixture
async def resolved_conversation(db, customer) -> Conversation:
    c = Conversation(
        id=str(uuid.uuid4()),
        customer_id=customer.id,
        status="resolved",
    )
    db.add(c)
    await db.flush()
    return c


@pytest.fixture
async def conversation_with_messages(db, customer) -> Conversation:
    conv = Conversation(
        id=str(uuid.uuid4()),
        customer_id=customer.id,
        status="resolved",
    )
    db.add(conv)
    await db.flush()

    for role, content, agent_type in [
        ("customer", "Hello, I need help.", None),
        ("agent", "How can I help?", "knowledge"),
    ]:
        db.add(Message(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            role=role,
            content=content,
            agent_type=agent_type,
        ))
    await db.flush()
    return conv

import uuid
import pytest

from backend.db.models import Product
from backend.db.seed import PRODUCTS


@pytest.fixture
async def catalog(db):
    """The seed catalog's IDs, so scenarios (which reference seed keys) resolve."""
    rows = [
        Product(id=PRODUCTS["laptop"], name="ProBook X15 Laptop", category="electronics",
                price=1299.99, return_window_days=14, warranty_months=12, metadata_={}),
        Product(id=PRODUCTS["phone"], name="Galaxy Nova 5G", category="electronics",
                price=799.99, return_window_days=14, warranty_months=12, metadata_={}),
        Product(id=PRODUCTS["headphones"], name="SoundMax Pro Headphones", category="electronics",
                price=249.99, return_window_days=14, warranty_months=6, metadata_={}),
        Product(id=PRODUCTS["t_shirt"], name="Classic Cotton T-Shirt", category="clothing",
                price=29.99, return_window_days=30, warranty_months=None, metadata_={}),
        Product(id=PRODUCTS["jeans"], name="Slim Fit Denim Jeans", category="clothing",
                price=79.99, return_window_days=30, warranty_months=None, metadata_={}),
        Product(id=PRODUCTS["blender"], name="PowerBlend Pro", category="home_goods",
                price=89.99, return_window_days=30, warranty_months=12, metadata_={}),
        Product(id=PRODUCTS["phone_case"], name="Rugged Phone Case", category="accessories",
                price=24.99, return_window_days=30, warranty_months=None, final_sale=True, metadata_={}),
        Product(id=PRODUCTS["desk_lamp"], name="LED Desk Lamp", category="home_goods",
                price=59.99, return_window_days=30, warranty_months=12, metadata_={}),
    ]
    for p in rows:
        db.add(p)
    await db.flush()
    await db.commit()
    return {p.id: p for p in rows}

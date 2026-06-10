"""
Dev seed data.

Creates, for the demo restaurant ("restaurant-a"):
  - a handful of menu items
  - 5 physical tables (prints their UUIDs so you can build QR codes)

Also seeds a *second* restaurant ("restaurant-b") with one menu item, purely so
you can verify tenant isolation (GET /api/menu must never show its item).

Run:        python -m app.seed
Re-run:     wipe-safe — it skips seeding if data already exists.
"""

from decimal import Decimal

from app.config import DEFAULT_RESTAURANT_ID
from app.db import Base, SessionLocal, engine
from app.models import MenuItem, Table

# (name, price, category, description, dietary_tags)
SAMPLE_MENU = [
    ("Bruschetta", "8.50", "appetizers", "Grilled bread, tomato, basil, olive oil.", ["vegetarian", "vegan"]),
    ("Calamari", "11.00", "appetizers", "Crispy fried squid with lemon aioli.", []),
    ("Margherita Pizza", "13.50", "mains", "San Marzano tomato, mozzarella, basil.", ["vegetarian"]),
    ("Spaghetti Carbonara", "15.00", "mains", "Egg, pecorino, guanciale, black pepper.", []),
    ("Grilled Salmon", "19.50", "mains", "Atlantic salmon, seasonal vegetables.", ["gluten-free"]),
    ("Tiramisu", "7.00", "desserts", "Classic mascarpone & espresso.", ["vegetarian"]),
    ("Panna Cotta", "6.50", "desserts", "Vanilla cream, berry coulis.", ["vegetarian", "gluten-free"]),
    ("Espresso", "3.00", "drinks", "Double shot.", ["vegan", "gluten-free"]),
    ("House Red Wine", "6.00", "drinks", "Glass of house red.", ["vegan"]),
]

NUM_TABLES = 5


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing = db.query(MenuItem).filter(
            MenuItem.restaurant_id == DEFAULT_RESTAURANT_ID
        ).count()
        if existing:
            print(f"Menu already has {existing} items — skipping seed.")
            return

        # Menu for the demo restaurant.
        for name, price, category, description, tags in SAMPLE_MENU:
            db.add(
                MenuItem(
                    restaurant_id=DEFAULT_RESTAURANT_ID,
                    name=name,
                    price=Decimal(price),
                    category=category,
                    description=description,
                    dietary_tags=tags,
                )
            )

        # Tables for the demo restaurant.
        tables = [
            Table(restaurant_id=DEFAULT_RESTAURANT_ID, number=n)
            for n in range(1, NUM_TABLES + 1)
        ]
        db.add_all(tables)

        # A second tenant, to prove isolation.
        db.add(
            MenuItem(
                restaurant_id="restaurant-b",
                name="SECRET other-restaurant dish",
                price=Decimal("99.00"),
                category="mains",
            )
        )

        db.commit()

        print(f"Seeded {len(SAMPLE_MENU)} menu items for {DEFAULT_RESTAURANT_ID}.")
        print(f"Seeded {NUM_TABLES} tables (QR url = /t/<id>):")
        for t in db.query(Table).filter(
            Table.restaurant_id == DEFAULT_RESTAURANT_ID
        ).order_by(Table.number).all():
            print(f"   Table {t.number}: {t.id}")
        print("Seeded 1 menu item for restaurant-b (isolation check).")
    finally:
        db.close()


if __name__ == "__main__":
    seed()

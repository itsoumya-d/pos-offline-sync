import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from possync.api import create_app
from possync.db import Base
from possync.service import ConflictError, NotFoundError, PosSyncService, ValidationError


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    yield s
    s.close()


@pytest.fixture()
def client(session):
    factory = lambda: session
    keys = {"k-admin": "admin", "k-cashier": "cashier", "k-viewer": "viewer"}
    app = create_app(session_factory=factory, api_keys=keys, init_database=False)
    return TestClient(app)


def setup_store_and_ingredients(session):
    service = PosSyncService(session, actor="setup_user")
    store = service.create_store("Epsom Sushi Store")
    # Register ingredients as stock items
    service.upsert_product(store.id, "ING-SALMON", "Fresh Salmon Fillet", 25.0, 5000)  # 5000g
    service.upsert_product(store.id, "ING-RICE", "Seasoned Sushi Rice", 5.0, 10000)   # 10000g
    service.upsert_product(store.id, "ING-NORI", "Nori Seaweed Sheets", 0.5, 500)      # 500 sheets
    return service, store


def test_recipe_lifecycle_and_conflict(session):
    service, store = setup_store_and_ingredients(session)

    ingredients = [
        {"ingredient_sku": "ING-SALMON", "quantity_per_serving": 50.0, "unit": "grams"},
        {"ingredient_sku": "ING-RICE", "quantity_per_serving": 100.0, "unit": "grams"},
        {"ingredient_sku": "ING-NORI", "quantity_per_serving": 1.0, "unit": "sheets"},
    ]

    recipe = service.create_recipe(
        store_id=store.id,
        recipe_code="REC-SALMON-ROLL",
        name="Fresh Salmon Roll",
        yield_servings=1,
        ingredients=ingredients,
    )

    assert recipe.recipe_code == "REC-SALMON-ROLL"
    assert recipe.yield_servings == 1

    # Conflict on duplicate
    with pytest.raises(ConflictError, match="already exists"):
        service.create_recipe(store.id, "REC-SALMON-ROLL", "Dup Roll", 1, ingredients)

    # Retrieval
    data = service.get_recipe(store.id, "REC-SALMON-ROLL")
    assert data["name"] == "Fresh Salmon Roll"
    assert len(data["ingredients"]) == 3


def test_recipe_unknown_ingredient(session):
    service, store = setup_store_and_ingredients(session)
    bad_ingredients = [
        {"ingredient_sku": "NON-EXISTENT-SKU", "quantity_per_serving": 10.0, "unit": "grams"}
    ]
    with pytest.raises(NotFoundError, match="not registered in store inventory"):
        service.create_recipe(store.id, "REC-UNKNOWN", "Unknown Item", 1, bad_ingredients)


def test_recipe_deduction_and_stock_limits(session):
    service, store = setup_store_and_ingredients(session)

    ingredients = [
        {"ingredient_sku": "ING-SALMON", "quantity_per_serving": 50.0, "unit": "grams"},
        {"ingredient_sku": "ING-RICE", "quantity_per_serving": 100.0, "unit": "grams"},
    ]
    service.create_recipe(store.id, "REC-BENTO-BOWL", "Salmon Bento Bowl", 1, ingredients)

    # Prepare 10 portions -> deducts 500g salmon and 1000g rice
    result = service.deduct_recipe_ingredients(store.id, "REC-BENTO-BOWL", portions=10)
    assert result["portions_prepared"] == 10
    assert len(result["deductions"]) == 2

    # Verify inventory was updated
    salmon = service.session.query(
        __import__("possync.tables", fromlist=["ProductRow"]).ProductRow
    ).filter_by(store_id=store.id, sku="ING-SALMON").one()
    assert salmon.stock == 4500  # 5000 - 500

    # Insufficient stock test: attempt to prepare 1000 portions (needs 50,000g salmon, only 4,500g available)
    with pytest.raises(ValidationError, match="insufficient stock"):
        service.deduct_recipe_ingredients(store.id, "REC-BENTO-BOWL", portions=1000)


def test_recipe_api_endpoints(client, session):
    # Setup via service
    service, store = setup_store_and_ingredients(session)

    # Admin creates recipe via POST /recipes
    resp = client.post(
        "/recipes",
        headers={"X-API-Key": "k-admin"},
        json={
            "store_id": store.id,
            "recipe_code": "REC-CHICKEN-ON-RICE",
            "name": "Chicken On Rice Bento",
            "yield_servings": 1,
            "ingredients": [
                {"ingredient_sku": "ING-RICE", "quantity_per_serving": 150.0, "unit": "grams"}
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["recipe_code"] == "REC-CHICKEN-ON-RICE"

    # Viewer queries recipe via GET /recipes/{store_id}/{recipe_code}
    resp_get = client.get(
        f"/recipes/{store.id}/REC-CHICKEN-ON-RICE",
        headers={"X-API-Key": "k-viewer"},
    )
    assert resp_get.status_code == 200
    assert resp_get.json()["name"] == "Chicken On Rice Bento"

    # Cashier deducts ingredients via POST /recipes/{recipe_code}/deduct
    resp_deduct = client.post(
        "/recipes/REC-CHICKEN-ON-RICE/deduct",
        headers={"X-API-Key": "k-cashier"},
        json={"store_id": store.id, "portions": 5},
    )
    assert resp_deduct.status_code == 200
    assert resp_deduct.json()["portions_prepared"] == 5

    # Check audit log contains both actions
    resp_audit = client.get("/audit", headers={"X-API-Key": "k-admin"})
    assert resp_audit.status_code == 200
    actions = [a["action"] for a in resp_audit.json()["audit"]]
    assert "recipe_created" in actions
    assert "recipe_ingredients_deducted" in actions

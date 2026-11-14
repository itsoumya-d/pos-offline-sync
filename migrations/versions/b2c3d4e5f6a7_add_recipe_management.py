"""add recipe management and ingredient tracking

Revision ID: b2c3d4e5f6a7
Revises: 5c6e2de4e32a
Create Date: 2026-11-14 11:17:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = '5c6e2de4e32a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'recipes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('store_id', sa.Integer(), nullable=False),
        sa.Column('recipe_code', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('yield_servings', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('store_id', 'recipe_code', name='uq_recipe_store_code')
    )
    op.create_index(op.f('ix_recipes_recipe_code'), 'recipes', ['recipe_code'], unique=False)
    op.create_index(op.f('ix_recipes_store_id'), 'recipes', ['store_id'], unique=False)

    op.create_table(
        'recipe_ingredients',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('recipe_id', sa.Integer(), nullable=False),
        sa.Column('ingredient_sku', sa.String(length=80), nullable=False),
        sa.Column('quantity_per_serving', sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column('unit', sa.String(length=20), nullable=False, server_default='grams'),
        sa.ForeignKeyConstraint(['recipe_id'], ['recipes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_recipe_ingredients_ingredient_sku'), 'recipe_ingredients', ['ingredient_sku'], unique=False)
    op.create_index(op.f('ix_recipe_ingredients_recipe_id'), 'recipe_ingredients', ['recipe_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_recipe_ingredients_recipe_id'), table_name='recipe_ingredients')
    op.drop_index(op.f('ix_recipe_ingredients_ingredient_sku'), table_name='recipe_ingredients')
    op.drop_table('recipe_ingredients')
    op.drop_index(op.f('ix_recipes_store_id'), table_name='recipes')
    op.drop_index(op.f('ix_recipes_recipe_code'), table_name='recipes')
    op.drop_table('recipes')

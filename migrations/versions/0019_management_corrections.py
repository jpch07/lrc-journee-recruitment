"""Add an independent, audited management correction layer. No score backfill."""
import sqlalchemy as sa
from alembic import op

revision = '0019_management_corrections'
down_revision = '0018_dynamic_general_factors'
branch_labels = None
depends_on = None


def upgrade():
    if 'management_corrections' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'management_corrections',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('system_id', sa.String(36), sa.ForeignKey('assessment_systems.id', ondelete='CASCADE'), nullable=False),
        sa.Column('journey_id', sa.String(36), sa.ForeignKey('journeys.id', ondelete='CASCADE'), nullable=False),
        sa.Column('recruit_id', sa.String(36), sa.ForeignKey('recruits.id', ondelete='CASCADE'), nullable=False),
        sa.Column('criterion_values_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('color_key', sa.String(40), nullable=True),
        sa.Column('configuration_signature', sa.String(64), nullable=False),
        sa.Column('configuration_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_by', sa.String(200), nullable=False),
        sa.UniqueConstraint('system_id', 'journey_id', 'recruit_id', name='uq_management_correction_scope'),
    )
    op.create_index('ix_management_correction_journey', 'management_corrections', ['system_id', 'journey_id'])


def downgrade():
    raise RuntimeError('Management correction history must be preserved; use a forward migration.')

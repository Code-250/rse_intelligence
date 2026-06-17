from alembic import op
import sqlalchemy as sa
revision = 'fda_002'
down_revision = None
branch_labels = None
depends_on = None
def upgrade():
    op.create_table('fda_users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=100), nullable=False),
        sa.Column('hashed_password', sa.String(length=100), nullable=False),
        sa.Column('plan', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )
    op.create_table('fda_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=100), nullable=False),
        sa.Column('storage_path', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['fda_users.id'], ondelete='CASCADE')
    )
    op.create_table('fda_analyses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('raw_ocr', sa.Text(), nullable=False),
        sa.Column('structured_data', sa.Text(), nullable=False),
        sa.Column('ai_summary', sa.Text(), nullable=False),
        sa.Column('model_used', sa.String(length=100), nullable=False),
        sa.Column('processing_ms', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['document_id'], ['fda_documents.id'], ondelete='CASCADE')
    )
    op.create_table('fda_usage',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('month', sa.String(length=100), nullable=False),
        sa.Column('document_count', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['fda_users.id'], ondelete='CASCADE')
    )
def downgrade():
    op.drop_table('fda_usage')
    op.drop_table('fda_analyses')
    op.drop_table('fda_documents')
    op.drop_table('fda_users')
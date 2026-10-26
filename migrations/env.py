"""Release-owned schema upgrades; application startup never creates or resets tables."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

from verdant.persistence import Base

url = os.environ["VERDANT_DATABASE_URL"]
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()

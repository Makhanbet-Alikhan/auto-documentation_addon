# -*- coding: utf-8 -*-
"""
Post-install / post-upgrade hooks for dpf_docs.

The post_migrate hook fires after every upgrade and cleans up any
legacy NOT NULL constraints that Odoo may have added in a previous
schema version but are now unwanted (e.g. after we removed required=True
from project_selection on doc.project.picker.wizard).
"""
import logging

_logger = logging.getLogger(__name__)


def post_migrate(cr, registry):
    """
    Drop leftover NOT NULL constraints from wizard transient tables.

    Safe to run multiple times — uses ALTER TABLE ... DROP NOT NULL which
    is a no-op if the column is already nullable.
    """
    _fix_nullable(cr, 'doc_project_picker_wizard', 'project_selection')
    _fix_nullable(cr, 'doc_project_picker_wizard', 'project_name')


def _fix_nullable(cr, table, column):
    """Make *column* in *table* nullable if table and column exist."""
    cr.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = %s
          AND column_name = %s
          AND is_nullable = 'NO'
        """,
        (table, column),
    )
    if cr.fetchone():
        try:
            cr.execute(
                'ALTER TABLE "%s" ALTER COLUMN "%s" DROP NOT NULL' % (table, column)
            )
            _logger.info(
                'post_migrate: dropped NOT NULL on %s.%s', table, column
            )
        except Exception as e:
            _logger.warning(
                'post_migrate: could not drop NOT NULL on %s.%s: %s',
                table, column, e,
            )

# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now


class CoffeeQRSequence(Document):
    def get_next(self, n=1):
        """Reserve ``n`` harvester numbers and return the base counter value.

        Issued IDs are ``base + 1 .. base + n``. Commits immediately so
        concurrent callers cannot hand out the same number.
        """
        n = int(n)
        if n < 1:
            n = 1
        base = self.harvester_counter or 0
        self.harvester_counter = base + n
        self.last_updated = now()
        self.last_updated_by = frappe.session.user
        self.save(ignore_permissions=True)
        frappe.db.commit()
        return base

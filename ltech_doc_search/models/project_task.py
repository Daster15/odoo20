from odoo import models


class ProjectTask(models.Model):
    _inherit = "project.task"

    def write(self, vals):
        res = super().write(vals)
        if "project_id" in vals:
            # task moved -> reindex its documents with the new project_id
            self.env["ir.attachment"].sudo().search([
                ("res_model", "=", "project.task"), ("res_id", "in", self.ids),
            ]).write({"solr_state": "pending"})
        return res

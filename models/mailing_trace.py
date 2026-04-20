# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class MailingTrace(models.Model):
    _inherit = 'mailing.trace'
    _description = 'Rastreo de Correo con Soporte SES'
    
    ses_message_id = fields.Char("SES Message-ID", index=True, help="ID de Mensaje de Amazon SES para rastreo")
import logging
import re
import html

from odoo import api, models, tools, fields

_logger = logging.getLogger(__name__)


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    MAIL_HEADER_MSGID_RE = re.compile(r'<([^>]+)>')

    @api.model
    def _message_route_process(self, message, message_dict, routes):
        if routes:
            thread_references = message_dict['references'] or message_dict['in_reply_to']
            msg_references = MAIL_HEADER_MSGID_RE.findall(thread_references or "")

            # Normalizar IDs entrantes eliminando el dominio
            normalized_refs = [ref.split('@')[0] for ref in msg_references]

            # Buscar coincidencias en message_id
            self.env['mailing.trace'].set_opened(domain=[
                ('message_id', 'in', msg_references)
            ])
            self.env['mailing.trace'].set_replied(domain=[
                ('message_id', 'in', msg_references)
            ])

            # Buscar coincidencias en SES message ID normalizado
            self.env['mailing.trace'].set_opened(domain=[
                ('ses_message_id', 'ilike', normalized_refs[0])
            ])
            self.env['mailing.trace'].set_replied(domain=[
                ('ses_message_id', 'ilike', normalized_refs[0])
            ])

        return super()._message_route_process(message, message_dict, routes)


    @api.model
    def _routing_handle_bounce(self, email_message, message_dict):
        """Override _routing_handle_bounce method to process if send mail with SES, message_id had been changed"""

        bounced_msg_ids = message_dict.get('bounced_msg_ids', [])
        
        # DEBUG: Log what we're searching for
        _logger.info(f"[SES BOUNCE DEBUG] bounced_msg_ids from Odoo: {bounced_msg_ids}")
        
        # Translate SES message IDs to original Odoo message IDs before calling super()
        # This allows Odoo's core bounce handler to find mail.mail records correctly
        if bounced_msg_ids:
            # SES changes the domain: stored as @us-east-1.amazonses.com but bounces come as @email.amazonses.com
            # So we need to search by the message ID part only (before @)
            traces_with_ses_ids = self.env['mailing.trace']
            
            for bounced_id in bounced_msg_ids:
                # Extract just the message ID portion (before @)
                # Example: <0100019ae9321ea7-...@email.amazonses.com> -> 0100019ae9321ea7-...
                msg_id_part = bounced_id.strip('<>').split('@')[0]
                _logger.info(f"[SES BOUNCE DEBUG] Searching for message ID part: {msg_id_part}")
                
                # Search for traces where ses_message_id contains this ID part
                traces = self.env['mailing.trace'].search([
                    ('ses_message_id', 'ilike', msg_id_part)
                ])
                traces_with_ses_ids |= traces
            
            # DEBUG: Log what we found
            _logger.info(f"[SES BOUNCE DEBUG] Found {len(traces_with_ses_ids)} traces with SES IDs")
            if traces_with_ses_ids:
                _logger.info(f"[SES BOUNCE DEBUG] Trace ses_message_ids: {traces_with_ses_ids.mapped('ses_message_id')}")
                _logger.info(f"[SES BOUNCE DEBUG] Trace message_ids: {traces_with_ses_ids.mapped('message_id')}")
            
            # Extract the original Odoo message IDs from matching traces
            original_msg_ids = traces_with_ses_ids.mapped('message_id')
            
            # Add original message IDs to bounced_msg_ids so Odoo's core handler can find them
            if original_msg_ids:
                # Extend the list with original IDs (avoid duplicates)
                extended_bounced_msg_ids = list(set(bounced_msg_ids + original_msg_ids))
                message_dict['bounced_msg_ids'] = extended_bounced_msg_ids
                _logger.info(f"[SES BOUNCE DEBUG] Extended bounced_msg_ids: {extended_bounced_msg_ids}")

        super(MailThread, self)._routing_handle_bounce(email_message, message_dict)

        # Fallback: handle bounces directly in mailing.trace if not found in mail.mail
        bounced_msg_ids = message_dict.get('bounced_msg_ids', [])
        traces_by_message_id = self.env['mailing.trace'].search([('message_id', 'in', bounced_msg_ids)])
        if bounced_msg_ids and not traces_by_message_id:
            # Same logic: search by message ID part only
            for bounced_id in bounced_msg_ids:
                msg_id_part = bounced_id.strip('<>').split('@')[0]
                self.env['mailing.trace'].set_bounced(
                    domain=[('ses_message_id', 'ilike', msg_id_part)],
                    bounce_message=tools.html2plaintext(message_dict.get('body') or '')
                )

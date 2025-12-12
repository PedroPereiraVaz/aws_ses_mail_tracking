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
            msg_references = self.MAIL_HEADER_MSGID_RE.findall(thread_references or "")

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
            if normalized_refs:
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

        # SES Bounce/Complaint Analysis (RFC 3464 & RFC 5965)
        bounce_details = []
        try:
            # Check Root Headers for Report Type (Fallback)
            is_generic_complaint = False
            is_generic_bounce = False
            
            if email_message.get_content_type() == 'multipart/report':
                report_type = email_message.get_param('report-type')
                if report_type == 'feedback-report':
                    is_generic_complaint = True
                elif report_type == 'delivery-status':
                    is_generic_bounce = True

            if email_message.is_multipart():
                from email.parser import HeaderParser
                
                for part in email_message.walk():
                    content_type = part.get_content_type()
                    
                    # 1. RFC 5965: Feedback Report (Complaint)
                    if content_type == 'message/feedback-report':
                        feedback_type = part.get('Feedback-Type')
                        # If header not found on the part itself, parse the payload
                        if not feedback_type:
                            payload = part.get_payload()
                            if isinstance(payload, str):
                                msg_block = HeaderParser().parsestr(payload)
                                feedback_type = msg_block.get('Feedback-Type')
                        
                        if feedback_type:
                            bounce_details.append(f"Complaint: {feedback_type}")
                            _logger.info(f"[SES BOUNCE] RFC 5965 Complaint: {feedback_type}")

                    # 2. RFC 3464: Delivery Status Notification (Bounce)
                    elif content_type == 'message/delivery-status':
                        payload = part.get_payload()
                        status_code = None
                        
                        if isinstance(payload, list):
                            # Rare, but if multipart
                            for subpart in payload:
                                if subpart.get('Status'):
                                    status_code = subpart.get('Status')
                        elif isinstance(payload, str):
                            # DSN format: Header blocks separated by blank lines
                            # Block 1: Per-Message fields
                            # Block 2+: Per-Recipient fields (contains Action, Status)
                            blocks = payload.split('\n\n')
                            for block in blocks:
                                msg_block = HeaderParser().parsestr(block)
                                if msg_block.get('Status'):
                                    status_code = msg_block.get('Status')
                                    # We take the first status found in a recipient block
                                    break
                                    
                        if status_code:
                            # 5.X.X = Hard, 4.X.X = Soft
                            if status_code.startswith('5'):
                                bounce_details.append(f"Hard Bounce (Code: {status_code})")
                            elif status_code.startswith('4'):
                                bounce_details.append(f"Soft Bounce (Code: {status_code})")
                            _logger.info(f"[SES BOUNCE] RFC 3464 Status: {status_code}")

            # Fallbacks if detailed parsing found nothing but headers matched
            if not bounce_details:
                if is_generic_complaint:
                    bounce_details.append("Complaint (Generic/SES)")
                elif is_generic_bounce:
                    bounce_details.append("Bounce (Generic/SES)")

            if bounce_details:
                detail_str = " | ".join(bounce_details)
                current_body = message_dict.get('body') or ''
                message_dict['body'] = f"<p><b>SES Report: {detail_str}</b></p><br/>{current_body}"
                _logger.info(f"[SES BOUNCE] Updated failure reason with: {detail_str}")

        except Exception as e:
            _logger.error(f"[SES BOUNCE] Error analyzing bounce details: {e}")
        
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

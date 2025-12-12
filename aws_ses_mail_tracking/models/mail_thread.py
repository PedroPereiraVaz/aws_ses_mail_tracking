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
        """Sobrescribe el método _routing_handle_bounce para procesar si el envío con SES cambió el message_id"""

        bounced_msg_ids = message_dict.get('bounced_msg_ids', [])

        # Análisis de Rebotes/Quejas SES (RFC 3464 y RFC 5965)
        bounce_details = []
        try:
            # Verificar cabeceras raíz para tipo de reporte (Respaldo)
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
                    
                    # 1. RFC 5965: Reporte de Retroalimentación (Queja)
                    if content_type == 'message/feedback-report':
                        feedback_type = part.get('Feedback-Type')
                        # Si no se encuentra la cabecera en la parte misma, analizar la carga útil
                        if not feedback_type:
                            payload = part.get_payload()
                            if isinstance(payload, str):
                                msg_block = HeaderParser().parsestr(payload)
                                feedback_type = msg_block.get('Feedback-Type')
                        
                        if feedback_type:
                            bounce_details.append(f"Complaint: {feedback_type}")
                            _logger.info(f"[SES BOUNCE] RFC 5965 Complaint: {feedback_type}")

                    # 2. RFC 3464: Notificación de Estado de Entrega (Rebote)
                    elif content_type == 'message/delivery-status':
                        payload = part.get_payload()
                        status_code = None
                        
                        if isinstance(payload, list):
                            # Raro, pero si es multiparte
                            for subpart in payload:
                                if subpart.get('Status'):
                                    status_code = subpart.get('Status')
                        elif isinstance(payload, str):
                            # Formato DSN: Bloques de cabecera separados por líneas en blanco
                            # Bloque 1: Campos por mensaje
                            # Bloque 2+: Campos por destinatario (contiene Action, Status)
                            blocks = payload.split('\n\n')
                            for block in blocks:
                                msg_block = HeaderParser().parsestr(block)
                                if msg_block.get('Status'):
                                    status_code = msg_block.get('Status')
                                    # Tomamos el primer estado encontrado en un bloque de destinatario
                                    break
                                    
                        if status_code:
                            # 5.X.X = Hard, 4.X.X = Soft
                            if status_code.startswith('5'):
                                bounce_details.append(f"Hard Bounce (Code: {status_code})")
                            elif status_code.startswith('4'):
                                bounce_details.append(f"Soft Bounce (Code: {status_code})")
                            _logger.info(f"[SES BOUNCE] RFC 3464 Status: {status_code}")

            # Respaldos si el análisis detallado no encontró nada pero las cabeceras coincidieron
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
        
        # DEBUG: Registrar lo que estamos buscando
        _logger.info(f"[SES BOUNCE DEBUG] bounced_msg_ids from Odoo: {bounced_msg_ids}")
        
        # Traducir IDs de mensajes SES a IDs de Odoo originales antes de llamar a super()
        # Esto permite que el manejador de rebotes estándar de Odoo encuentre los registros mail.mail correctamente
        if bounced_msg_ids:
            # SES cambia el dominio: almacenado como @us-east-1.amazonses.com pero los rebotes llegan como @email.amazonses.com
            # Así que necesitamos buscar solo por la parte del ID del mensaje (antes de @)
            traces_with_ses_ids = self.env['mailing.trace']
            
            for bounced_id in bounced_msg_ids:
                # Extraer solo la porción del ID de mensaje (antes de @)
                # Example: <0100019ae9321ea7-...@email.amazonses.com> -> 0100019ae9321ea7-...
                msg_id_part = bounced_id.strip('<>').split('@')[0]
                _logger.info(f"[SES BOUNCE DEBUG] Searching for message ID part: {msg_id_part}")
                
                # Buscar rastros donde ses_message_id contenga esta parte del ID
                traces = self.env['mailing.trace'].search([
                    ('ses_message_id', 'ilike', msg_id_part)
                ])
                traces_with_ses_ids |= traces
            
            # DEBUG: Registrar lo que encontramos
            _logger.info(f"[SES BOUNCE DEBUG] Found {len(traces_with_ses_ids)} traces with SES IDs")
            if traces_with_ses_ids:
                _logger.info(f"[SES BOUNCE DEBUG] Trace ses_message_ids: {traces_with_ses_ids.mapped('ses_message_id')}")
                _logger.info(f"[SES BOUNCE DEBUG] Trace message_ids: {traces_with_ses_ids.mapped('message_id')}")
            
            # Extraer los Message-IDs originales de Odoo de los rastros coincidentes
            original_msg_ids = traces_with_ses_ids.mapped('message_id')
            
            # Añadir Message-IDs originales a bounced_msg_ids para que el manejador de Odoo los encuentre
            if original_msg_ids:
                # Extender la lista con IDs originales (evitar duplicados)
                extended_bounced_msg_ids = list(set(bounced_msg_ids + original_msg_ids))
                message_dict['bounced_msg_ids'] = extended_bounced_msg_ids
                _logger.info(f"[SES BOUNCE DEBUG] Extended bounced_msg_ids: {extended_bounced_msg_ids}")

        super(MailThread, self)._routing_handle_bounce(email_message, message_dict)

        # Respaldo: manejar rebotes directamente en mailing.trace si no se encuentran en mail.mail
        bounced_msg_ids = message_dict.get('bounced_msg_ids', [])
        traces_by_message_id = self.env['mailing.trace'].search([('message_id', 'in', bounced_msg_ids)])
        if bounced_msg_ids and not traces_by_message_id:
            # Misma lógica: buscar solo por la parte del ID de mensaje
            for bounced_id in bounced_msg_ids:
                msg_id_part = bounced_id.strip('<>').split('@')[0]
                self.env['mailing.trace'].set_bounced(
                    domain=[('ses_message_id', 'ilike', msg_id_part)],
                    bounce_message=tools.html2plaintext(message_dict.get('body') or '')
                )

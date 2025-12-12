# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import re
import smtplib
import sys
import base64
import ssl
import idna
import copy

from OpenSSL import crypto as SSLCrypto
from OpenSSL.crypto import Error as SSLCryptoError, FILETYPE_PEM
from OpenSSL.SSL import Context as SSLContext, Error as SSLError

from odoo import api, models, modules, _, tools
from odoo.addons.base.models.ir_mail_server import is_ascii, MailDeliveryException, SMTP_TIMEOUT # Eliminamos ustr 
from odoo.exceptions import UserError
from odoo.addons.aws_ses_mail_tracking.libs import smtplib_inherit

_logger = logging.getLogger(__name__)
_test_logger = logging.getLogger('odoo.tests')


class IrMailServer(models.Model):
    _inherit = "ir.mail_server"

    def connect(self, host=None, port=None, user=None, password=None, encryption=None,
                smtp_from=None, ssl_certificate=None, ssl_private_key=None, smtp_debug=False, mail_server_id=None,
                allow_archived=False):
        """Sobrescribe el método connect para integrar la clase SMTP heredada"""

        # No conectar realmente si se está ejecutando en modo de prueba
        if modules.module.current_test:
            return

        mail_server = smtp_encryption = None
        if mail_server_id:
            mail_server = self.sudo().browse(mail_server_id)
            if not allow_archived and not mail_server.active:
                raise UserError(_('The server "%s" cannot be used because it is archived.', mail_server.display_name))
        elif not host:
            mail_server, smtp_from = self.sudo()._find_mail_server(smtp_from)

        if not mail_server:
            mail_server = self.env['ir.mail_server']
        ssl_context = None

        if mail_server and mail_server.smtp_authentication != "cli":
            smtp_server = mail_server.smtp_host
            smtp_port = mail_server.smtp_port
            if mail_server.smtp_authentication == "certificate":
                smtp_user = None
                smtp_password = None
            else:
                smtp_user = mail_server.smtp_user
                smtp_password = mail_server.smtp_pass
            smtp_encryption = mail_server.smtp_encryption
            smtp_debug = smtp_debug or mail_server.smtp_debug
            from_filter = mail_server.from_filter
            if (mail_server.smtp_authentication == "certificate"
               and mail_server.smtp_ssl_certificate
               and mail_server.smtp_ssl_private_key):
                try:
                    ssl_context = SSLContext(ssl.PROTOCOL_TLS)
                    smtp_ssl_certificate = base64.b64decode(mail_server.smtp_ssl_certificate)
                    certificate = SSLCrypto.load_certificate(FILETYPE_PEM, smtp_ssl_certificate)
                    smtp_ssl_private_key = base64.b64decode(mail_server.smtp_ssl_private_key)
                    private_key = SSLCrypto.load_privatekey(FILETYPE_PEM, smtp_ssl_private_key)
                    ssl_context.use_certificate(certificate)
                    ssl_context.use_privatekey(private_key)
                    # Verificar que la clave privada coincida con el certificado
                    ssl_context.check_privatekey()
                except SSLCryptoError as e:
                    raise UserError(_('The private key or the certificate is not a valid file. \n%s', str(e)))
                except SSLError as e:
                    raise UserError(_('Could not load your certificate / private key. \n%s', str(e)))

        else:
            # se pasaron parámetros smtp individuales o nada y no hay servidor predeterminado
            smtp_server = host or tools.config.get('smtp_server')
            smtp_port = tools.config.get('smtp_port', 25) if port is None else port
            smtp_user = user or tools.config.get('smtp_user')
            smtp_password = password or tools.config.get('smtp_password')
            if mail_server:
                from_filter = mail_server.from_filter
            else:
                from_filter = self.env['ir.mail_server']._get_default_from_filter()

            smtp_encryption = encryption
            if smtp_encryption is None and tools.config.get('smtp_ssl'):
                smtp_encryption = 'starttls' # smtp_ssl => STARTTLS desde la v7
            smtp_ssl_certificate_filename = ssl_certificate or tools.config.get('smtp_ssl_certificate_filename')
            smtp_ssl_private_key_filename = ssl_private_key or tools.config.get('smtp_ssl_private_key_filename')

            if smtp_ssl_certificate_filename and smtp_ssl_private_key_filename:
                try:
                    ssl_context = SSLContext(ssl.PROTOCOL_TLS)
                    ssl_context.use_certificate_chain_file(smtp_ssl_certificate_filename)
                    ssl_context.use_privatekey_file(smtp_ssl_private_key_filename)
                    # Verificar que la clave privada coincida con el certificado
                    ssl_context.check_privatekey()
                except SSLCryptoError as e:
                    raise UserError(_('The private key or the certificate is not a valid file. \n%s', str(e)))
                except SSLError as e:
                    raise UserError(_('Could not load your certificate / private key. \n%s', str(e)))

        if not smtp_server:
            raise UserError(
                (_("Missing SMTP Server") + "\n" +
                 _("Please define at least one SMTP server, "
                   "or provide the SMTP parameters explicitly.")))

        if smtp_encryption == 'ssl':
            if 'SMTP_SSL' not in smtplib.__all__:
                raise UserError(
                    _("Your Odoo Server does not support SMTP-over-SSL. "
                      "You could use STARTTLS instead. "
                       "If SSL is needed, an upgrade to Python 2.6 on the server-side "
                       "should do the trick."))
            connection = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=SMTP_TIMEOUT)
        else:
            # Cambio aquí: usar smtplib_inherit para funcionalidad extendida
            connection = smtplib_inherit.SMTPInherit(smtp_server, smtp_port, timeout=SMTP_TIMEOUT)
            #####

        connection.set_debuglevel(smtp_debug)
        if smtp_encryption == 'starttls':
            # starttls() ejecutará ehlo() si es necesario primero
            # y descartará la lista previa de servicios
            # después de ejecutar exitosamente el comando STARTTLS,
            # (según RFC 3207) así que por ejemplo cualquier capacidad AUTH
            # que aparezca solo en canales encriptados
            # será detectada correctamente para el siguiente paso
            connection.starttls(context=ssl_context)

        if smtp_user:
            # Intentar autenticación - lanzará excepción si el servicio AUTH no es soportado
            local, at, domain = smtp_user.rpartition('@')
            if at:
                smtp_user = local + at + idna.encode(domain).decode('ascii')
            mail_server._smtp_login(connection, smtp_user, smtp_password or '')

        # Algunos métodos SMTP no verifican si EHLO/HELO fue enviado.
        # De todos modos, como puede haber sido enviado por login(), todos los usos subsiguientes deberían considerar este comando como enviado.
        connection.ehlo_or_helo_if_needed()

        # Almacenar el "from_filter" del servidor de correo / argumento odoo-bin para saber si
        # necesitamos cambiar las cabeceras FROM o no cuando preparemos el mensaje de correo
        connection.from_filter = from_filter
        connection.smtp_from = smtp_from

        return connection

    @api.model
    def send_email(self, message, mail_server_id=None, smtp_server=None, smtp_port=None,
                   smtp_user=None, smtp_password=None, smtp_encryption=None,
                   smtp_ssl_certificate=None, smtp_ssl_private_key=None,
                   smtp_debug=False, smtp_session=None):
        """Reescribir el método send_mail para cambiar el message_id"""

        smtp = smtp_session
        if not smtp:
            smtp = self.connect(
                smtp_server, smtp_port, smtp_user, smtp_password, smtp_encryption,
                smtp_from=message['From'], ssl_certificate=smtp_ssl_certificate, ssl_private_key=smtp_ssl_private_key,
                smtp_debug=smtp_debug, mail_server_id=mail_server_id,)

        smtp_from, smtp_to_list, message = self._prepare_email_message(message, smtp)

        # ¡No enviar correos realmente en modo test!
        if modules.module.current_test:
            _test_logger.info("omitir envío de correo en modo test")
            return message['Message-Id']

        try:
            message_id = message['Message-Id']

            if sys.version_info < (3, 7, 4):
                # el código de plegado de cabeceras tiene errores y añade retornos de carro redundantes,
                # se arregló en 3.7.4 gracias a bpo-34424
                message_str = message.as_string()
                message_str = re.sub('\r+(?!\n)', '', message_str)

                mail_options = []
                if any((not is_ascii(addr) for addr in smtp_to_list + [smtp_from])):
                    # email no ascii encontrado, requiere extensión SMTPUTF8,
                    # el relay podría rechazarlo
                    mail_options.append("SMTPUTF8")
                smtp.sendmail(smtp_from, smtp_to_list, message_str, mail_options=mail_options)
            else:
                resp = smtp.send_message(message, smtp_from, smtp_to_list)
                # Cambio aquí: Actualizar SES Message-ID
                host_split = smtp._host.split(".")
                (region, domain) = host_split[1], f"{host_split[2]}.{host_split[3]}"
                if domain == "amazonaws.com":
                    ses_message_id = f"<{resp.decode().split(' ')[1]}@{region}.amazonses.com>"
                    _logger.info(f"[SES SEND DEBUG] Original message_id: {message_id}")
                    _logger.info(f"[SES SEND DEBUG] Generated ses_message_id: {ses_message_id}")
                    trace = self.env['mailing.trace'].search([('message_id', '=', message_id)])
                    _logger.info(f"[SES SEND DEBUG] Found {len(trace)} mailing.trace records for message_id")
                    if trace:
                        trace[0].ses_message_id = ses_message_id
                        _logger.info(f"[SES SEND DEBUG] Stored ses_message_id in trace ID: {trace[0].id}")
                    else:
                        _logger.warning(f"[SES SEND DEBUG] No mailing.trace found for message_id: {message_id} - ses_message_id not stored!")
                #####

            # do not quit() a pre-established smtp_session
            if not smtp_session:
                smtp.quit()
        except smtplib.SMTPServerDisconnected:
            raise
        except Exception as e:
            params = (str(smtp_server), e.__class__.__name__, str(e)) # modificamos ustr por str
            msg = _("Mail delivery failed via SMTP server '%s'.\n%s: %s", *params)
            _logger.info(msg)
            raise MailDeliveryException(_("Mail Delivery Failed"), msg)
        return message_id

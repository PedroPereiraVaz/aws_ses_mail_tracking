import re

from smtplib import SMTP, SMTP_SSL, CRLF, SMTPSenderRefused, SMTPRecipientsRefused, SMTPDataError


def _fix_eols(data):
    return re.sub(r'(?:\r\n|\n|\r(?!\n))', CRLF, data)


class _SESDataCaptureMixin:
    """Captura la respuesta del comando DATA (contiene el SES Message-ID)
    en ``self._ses_data_response`` para poder leerla tras send_message/sendmail
    sin depender de detalles de implementación de stdlib."""

    _ses_data_response = None

    def data(self, msg):
        code, resp = super().data(msg)
        if code == 250:
            self._ses_data_response = resp
        return code, resp


class SMTPInherit(_SESDataCaptureMixin, SMTP):

    def sendmail(self, from_addr, to_addrs, msg, mail_options=(),rcpt_options=()):
        self.ehlo_or_helo_if_needed()
        esmtp_opts = []
        if isinstance(msg, str):
            msg = _fix_eols(msg).encode('ascii')
        if self.does_esmtp:
            if self.has_extn('size'):
                esmtp_opts.append("size=%d" % len(msg))
            for option in mail_options:
                esmtp_opts.append(option)
        (code, resp) = self.mail(from_addr, esmtp_opts)
        if code != 250:
            if code == 421:
                self.close()
            else:
                self._rset()
            raise SMTPSenderRefused(code, resp, from_addr)
        senderrs = {}
        if isinstance(to_addrs, str):
            to_addrs = [to_addrs]
        for each in to_addrs:
            (code, resp) = self.rcpt(each, rcpt_options)
            if (code != 250) and (code != 251):
                senderrs[each] = (code, resp)
            if code == 421:
                self.close()
                raise SMTPRecipientsRefused(senderrs)
        if len(senderrs) == len(to_addrs):
            # el servidor rechazó todos nuestros destinatarios
            self._rset()
            raise SMTPRecipientsRefused(senderrs)
        (code, resp) = self.data(msg)
        if code != 250:
            if code == 421:
                self.close()
            else:
                self._rset()
            raise SMTPDataError(code, resp)
        # si llegamos aquí, entonces alguien recibió nuestro correo

        return resp


class SMTPInheritSSL(_SESDataCaptureMixin, SMTP_SSL):
    """Variante SSL (puerto 465) que también captura la respuesta DATA."""
    pass

import smtplib
import requests
from django.conf import settings
from django.core.mail import EmailMessage


class DeliveryError(Exception):
    pass


class DeliveryUnknown(Exception):
    pass


def configuration_error(channel):
    if channel == 'email':
        if settings.EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend' or not settings.EMAIL_HOST or not settings.DEFAULT_FROM_EMAIL:
            return 'Configure SMTP EMAIL_HOST, DEFAULT_FROM_EMAIL and credentials before sending.'
    elif not all([settings.WHATSAPP_ACCESS_TOKEN, settings.WHATSAPP_PHONE_NUMBER_ID, settings.WHATSAPP_GRAPH_VERSION, settings.WHATSAPP_TEMPLATE_NAME]):
        return 'Configure WhatsApp Business credentials and an approved document-header template before sending.'
    return ''


def send_email(recipient, document, pdf, filename):
    message = EmailMessage(
        subject=f"{document['documentType']} {document['number']}",
        body=f"Dear {document['customer']['contactPerson'] or document['customer']['name']},\n\nPlease find attached {document['documentType'].lower()} {document['number']}.\n\nRegards,\n{document['company']['tradingName']}",
        from_email=settings.DEFAULT_FROM_EMAIL, to=[recipient],
    )
    message.attach(filename, pdf, 'application/pdf')
    try:
        sent = message.send(fail_silently=False)
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
        raise DeliveryError('SMTP rejected authentication, sender or recipient. Check server settings.') from exc
    except Exception as exc:
        # A disconnect may happen after SMTP accepts DATA. Never blindly retry.
        raise DeliveryUnknown('SMTP response was interrupted. Check the mail provider before sending again.') from exc
    if sent != 1:
        raise DeliveryError('SMTP did not accept the email.')
    return ''


def send_whatsapp(recipient, document, pdf, filename):
    base = f'https://graph.facebook.com/{settings.WHATSAPP_GRAPH_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}'
    headers = {'Authorization': f'Bearer {settings.WHATSAPP_ACCESS_TOKEN}'}
    try:
        upload = requests.post(base + '/media', headers=headers,
            data={'messaging_product': 'whatsapp', 'type': 'application/pdf'},
            files={'file': (filename, pdf, 'application/pdf')}, timeout=(10, 45))
        if not upload.ok:
            raise DeliveryError('WhatsApp rejected the PDF upload. Check Business API settings.')
        media_id = upload.json()['id']
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise DeliveryError('WhatsApp PDF upload failed; no message was submitted.') from exc
    # Approved template: DOCUMENT header and body {{1}} = quotation number.
    payload = {'messaging_product': 'whatsapp', 'to': recipient.lstrip('+'), 'type': 'template', 'template': {
        'name': settings.WHATSAPP_TEMPLATE_NAME, 'language': {'code': settings.WHATSAPP_TEMPLATE_LANGUAGE},
        'components': [
            {'type': 'header', 'parameters': [{'type': 'document', 'document': {'id': media_id, 'filename': filename}}]},
            {'type': 'body', 'parameters': [{'type': 'text', 'text': document['number']}]},
        ],
    }}
    try:
        response = requests.post(base + '/messages', headers=headers, json=payload, timeout=(10, 45))
        if not response.ok:
            # 5xx responses may be ambiguous; do not automatically resend.
            if response.status_code >= 500:
                raise DeliveryUnknown('WhatsApp provider response is uncertain. Check Business message logs before retrying.')
            raise DeliveryError('WhatsApp rejected the message. Check template, recipient and Business API settings.')
        return response.json()['messages'][0]['id']
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        raise DeliveryUnknown('WhatsApp submission status is unknown. Check Business message logs before retrying.') from exc

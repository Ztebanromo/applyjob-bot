import smtplib
import os
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("bot.notifier")

import requests

class Notifier:
    def __init__(self):
        self.email_enabled = os.getenv("NOTIFY_EMAIL_ENABLED", "false").lower() == "true"
        self.email_user = os.getenv("NOTIFY_EMAIL_USER")
        self.email_pass = os.getenv("NOTIFY_EMAIL_PASS")
        self.email_to = os.getenv("NOTIFY_EMAIL_TO")
        self.smtp_server = os.getenv("NOTIFY_SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("NOTIFY_SMTP_PORT", "587"))
        
        self.webhook_url = os.getenv("NOTIFY_WEBHOOK_URL") # ntfy, discord, etc.

    def send_summary(self, portal: str, applied: int, errors: int, total_processed: int):
        """Envía un resumen de la ejecución actual."""
        subject = f"🚀 ApplyJob: {portal.capitalize()}"
        
        body = (
            f"✅ Postulaciones: {applied}\n"
            f"❌ Errores/Saltos: {errors}\n"
            f"📊 Total: {total_processed}\n"
            f"🔗 Dashboard: http://localhost:8000"
        )
        
        print(f"\n📢 NOTIFICACIÓN: {subject}\n{body}\n")
        
        if self.email_enabled:
            self._send_email(subject, body)
            
        if self.webhook_url:
            self._send_webhook(subject, body)

    def _send_webhook(self, title, message):
        try:
            # Soporte básico para ntfy.sh (muy recomendado para móvil)
            if "ntfy.sh" in self.webhook_url:
                requests.post(self.webhook_url, 
                            data=message.encode('utf-8'),
                            headers={"Title": title, "Priority": "high"})
            else:
                # Soporte genérico para Discord/Slack
                requests.post(self.webhook_url, json={"text": f"{title}\n{message}", "content": f"**{title}**\n{message}"})
            log.info("Notificación Webhook enviada.")
        except Exception as e:
            log.error(f"Error enviando Webhook: {e}")

    def _send_email(self, subject, body):
        try:
            msg = MIMEMultipart()
            msg['From'] = self.email_user
            msg['To'] = self.email_to or self.email_user
            msg['Subject'] = subject
            
            msg.attach(MIMEText(body, 'plain'))
            
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.email_user, self.email_pass)
            server.send_message(msg)
            server.quit()
            log.info("Email de notificación enviado con éxito.")
        except Exception as e:
            log.error(f"Error enviando email: {e}")

# Instancia global
notifier = Notifier()

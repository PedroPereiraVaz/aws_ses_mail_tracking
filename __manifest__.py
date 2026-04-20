# -*- coding: utf-8 -*-
{
    "name": "BerpIA - AWS SES mail tracking",
    "version": "18.0.1.0.0",
    "category": "BerpIA",
    "author": "Pedro Pereira Vaz",
    "website": "https://wavext.io",
    "summary": "Seguimiento completo de emails enviados mediante Amazon SES",
    "description": """
        Tracking Avanzado de Emails con AWS SES
        ========================================
        
        Este módulo profesional resuelve el problema de correlación de Message-IDs
        cuando se envían campañas de email marketing a través de Amazon Simple Email
        Service (AWS SES) con Odoo.
        
        🎯 Problema que Resuelve
        ------------------------
        AWS SES reemplaza el Message-ID original de Odoo durante el envío, lo que
        impide el seguimiento correcto de rebotes, respuestas y aperturas. Además,
        utiliza dominios diferentes para el envío y los rebotes, complicando aún
        más la correlación.
        
        ✨ Características Principales
        ------------------------------
        • Captura automática del Message-ID generado por AWS SES
        • Almacenamiento dual: ID original de Odoo + ID de SES
        • Normalización inteligente de dominios para búsquedas
        • Correlación precisa de rebotes y respuestas
        • Soporte completo para campañas de Mass Mailing
        • Sistema de logging detallado para debugging
        
        🔧 Componentes Técnicos
        -----------------------
        El módulo extiende los siguientes componentes de Odoo:
        
        • ir.mail_server: Captura la respuesta SMTP de SES con el nuevo Message-ID
        • mail.thread: Gestiona la correlación de rebotes y respuestas
        • mailing.trace: Almacena ambos identificadores para tracking completo
        • SMTP personalizado: Retorna la respuesta completa del servidor
        
        📊 Casos de Uso
        ---------------
        Ideal para empresas que:
        - Utilizan AWS SES como proveedor de email
        - Gestionan campañas de email marketing desde Odoo
        - Requieren estadísticas precisas de deliverability
        - Necesitan tracking detallado de interacciones con emails
        
        🚀 Instalación y Uso
        --------------------
        1. Instale el módulo desde Aplicaciones
        2. Configure su servidor SMTP de AWS SES en Ajustes > Técnico > Email
        3. ¡Listo! El tracking funciona automáticamente
        
        No requiere configuración adicional. El módulo detecta automáticamente
        cuando se está usando AWS SES y activa el tracking inteligente.
        
        📝 Notas Técnicas
        -----------------
        Compatible con Odoo 18 Community y Enterprise.
        
    """,
    "depends": [
        "mail",
        "mass_mailing",
    ],
    "data": [
        "views/mailing_trace_view.xml",
    ],
    "images": [],
    "installable": True,
    "application": False,
    "auto_install": False,
    "license": "LGPL-3",
}

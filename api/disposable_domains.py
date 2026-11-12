"""Well-known disposable / temporary email domains.

Extend at runtime with BUILDER_BLOCKED_EMAIL_DOMAINS (comma-separated) without
changing code. Subdomains of a listed domain are blocked as well.
"""

import os

DISPOSABLE_DOMAINS = frozenset(
    """
    0-mail.com 10minutemail.co.uk 10minutemail.com 10minutemail.net 20minutemail.com
    anonbox.net armyspy.com burnermail.io byom.de cool.fr.nf courriel.fr.nf crazymailing.com
    cuvox.de dayrep.com discard.email discardmail.com discardmail.de dispostable.com
    einrot.com email-fake.com emailfake.com emailondeck.com emailtemporanea.com
    emailtemporar.ro etranquil.com fakeinbox.com fakemail.net fakemailgenerator.com
    fleckens.hu getairmail.com getnada.com grr.la guerrillamail.biz guerrillamail.com
    guerrillamail.de guerrillamail.info guerrillamail.net guerrillamail.org
    guerrillamailblock.com gustr.com harakirimail.com inboxkitten.com jetable.fr.nf
    jetable.org jourrapide.com linshiyouxiang.net luxusmail.org mail.tm mailcatch.com
    maildrop.cc mailexpire.com mailforspam.com mailinator.com mailinator.net
    mailinator2.com mailmoat.com mailnesia.com mailnull.com mailpoof.com mailsac.com
    mailtemp.net mega.zik.dj minuteinbox.com mintemail.com moakt.com mohmal.com
    moncourrier.fr.nf monemail.fr.nf monmail.fr.nf mvrht.com mytemp.email nada.email
    nomail.xl.cx nospam.ze.tc pokemail.net rhyta.com sharklasers.com sofort-mail.de
    spam4.me spambox.us spamdecoy.net spamex.com spamfree24.org spamgourmet.com
    speed.1s.fr superrito.com teleworm.us temp-mail.io temp-mail.org tempail.com
    tempinbox.com tempmail.com tempmail.net tempmailaddress.com tempmailo.com
    temporary-mail.net tempr.email throwawaymail.com tmail.ws tmails.net tmpmail.net
    tmpmail.org trash-mail.com trashmail.com trashmail.de trashmail.io trashmail.net
    wegwerfemail.de wegwerfmail.de wegwerfmail.net yopmail.com yopmail.fr yopmail.net
    """.split()
)


def _extra_domains() -> set[str]:
    raw = os.getenv("BUILDER_BLOCKED_EMAIL_DOMAINS", "")
    return {item.strip().lower().lstrip("@") for item in raw.split(",") if item.strip()}


def is_disposable_email(email: str) -> bool:
    """True when the address uses a known disposable domain (or a subdomain of one)."""
    domain = email.strip().lower().rpartition("@")[2]
    if not domain:
        return False
    blocked = DISPOSABLE_DOMAINS | _extra_domains()
    parts = domain.split(".")
    return any(".".join(parts[index:]) in blocked for index in range(len(parts) - 1))

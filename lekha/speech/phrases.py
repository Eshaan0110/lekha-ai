"""What Lekha says on a call, in Kannada, Hindi and English. Pure: no I/O.

DRAFT WORDING. The consent notice must be approved by Group Legal before any real call
(plan: "Consent and retention wording is approved by Legal"), and the Kannada and Hindi
lines need review by native speakers.
"""

from __future__ import annotations

from enum import StrEnum

from lekha.actions import ProposalStatus, TicketCategory, TicketInputs


class Phrase(StrEnum):
    CONSENT = "consent"
    ASK_ID = "ask_id"
    ID_NOT_UNDERSTOOD = "id_not_understood"
    CODE_SENT = "code_sent"
    CODE_NOT_UNDERSTOOD = "code_not_understood"
    WRONG_CODE = "wrong_code"
    VERIFIED = "verified"
    DESCRIBE_ISSUE = "describe_issue"
    READBACK = "readback"
    TICKET_LOGGED = "ticket_logged"
    TICKET_CANCELLED = "ticket_cancelled"
    CANNOT_LOG = "cannot_log"
    NO_TICKETS = "no_tickets"
    ANSWER_SENT = "answer_sent"
    HANDOVER = "handover"
    GOODBYE = "goodbye"
    TOO_MANY = "too_many"


_PHRASES: dict[str, dict[Phrase, str]] = {
    "en": {
        Phrase.CONSENT: "Hello, this is Lekha. This call is handled by an automated system "
        "and a written record of it is kept.",
        Phrase.ASK_ID: "Please say your employee number.",
        Phrase.ID_NOT_UNDERSTOOD: "Sorry, I didn't get that. Please say the five digits of "
        "your employee number.",
        Phrase.CODE_SENT: "If that number is registered, a code has been sent to the phone "
        "registered with HR. Please say the six-digit code.",
        Phrase.CODE_NOT_UNDERSTOOD: "Sorry, I didn't get that. Please say the six digits.",
        Phrase.WRONG_CODE: "That code is not right. Please try again.",
        Phrase.VERIFIED: "Thank you, you are verified. Ask your question, or say "
        '"complaint" to log an issue.',
        Phrase.DESCRIBE_ISSUE: "Please describe the problem.",
        Phrase.READBACK: "I will log this: {title}. Category {category}, priority "
        "{priority}. Is that right? Please say yes or no.",
        Phrase.TICKET_LOGGED: "Your complaint has been sent for approval. Once an approver "
        "agrees, the ticket number will be sent to you by SMS.",
        Phrase.TICKET_CANCELLED: "All right, I have not logged it.",
        Phrase.CANNOT_LOG: "Sorry, I can't log a complaint for you.",
        Phrase.NO_TICKETS: "You have no complaints logged.",
        Phrase.ANSWER_SENT: "The full answer and its sources have been sent by SMS.",
        Phrase.HANDOVER: "I am connecting you to a person. Please hold.",
        Phrase.GOODBYE: "Thank you. Goodbye.",
        Phrase.TOO_MANY: "That's a lot of questions at once. Please ask again in a minute.",
    },
    "kn": {
        Phrase.CONSENT: "ನಮಸ್ಕಾರ, ಇದು ಲೇಖಾ. ಈ ಕರೆಯನ್ನು ಸ್ವಯಂಚಾಲಿತ ವ್ಯವಸ್ಥೆ ನಿರ್ವಹಿಸುತ್ತದೆ ಮತ್ತು "
        "ಅದರ ಲಿಖಿತ ದಾಖಲೆಯನ್ನು ಇಡಲಾಗುತ್ತದೆ.",
        Phrase.ASK_ID: "ದಯವಿಟ್ಟು ನಿಮ್ಮ ಉದ್ಯೋಗಿ ಸಂಖ್ಯೆಯನ್ನು ಹೇಳಿ.",
        Phrase.ID_NOT_UNDERSTOOD: "ಕ್ಷಮಿಸಿ, ಅರ್ಥವಾಗಲಿಲ್ಲ. ನಿಮ್ಮ ಉದ್ಯೋಗಿ ಸಂಖ್ಯೆಯ ಐದು ಅಂಕಿಗಳನ್ನು ಹೇಳಿ.",
        Phrase.CODE_SENT: "ಆ ಸಂಖ್ಯೆ ನೋಂದಾಯಿತವಾಗಿದ್ದರೆ, HR ನಲ್ಲಿ ನೋಂದಾಯಿಸಿದ ಫೋನ್‌ಗೆ ಒಂದು ಕೋಡ್ "
        "ಕಳುಹಿಸಲಾಗಿದೆ. ದಯವಿಟ್ಟು ಆರು ಅಂಕಿಯ ಕೋಡ್ ಹೇಳಿ.",
        Phrase.CODE_NOT_UNDERSTOOD: "ಕ್ಷಮಿಸಿ, ಅರ್ಥವಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಆರು ಅಂಕಿಗಳನ್ನು ಹೇಳಿ.",
        Phrase.WRONG_CODE: "ಆ ಕೋಡ್ ಸರಿಯಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೊಮ್ಮೆ ಪ್ರಯತ್ನಿಸಿ.",
        Phrase.VERIFIED: "ಧನ್ಯವಾದಗಳು, ನಿಮ್ಮ ಗುರುತು ದೃಢಪಟ್ಟಿದೆ. ನಿಮ್ಮ ಪ್ರಶ್ನೆ ಕೇಳಿ, ಅಥವಾ ಸಮಸ್ಯೆ "
        'ದಾಖಲಿಸಲು "ದೂರು" ಎಂದು ಹೇಳಿ.',
        Phrase.DESCRIBE_ISSUE: "ದಯವಿಟ್ಟು ಸಮಸ್ಯೆಯನ್ನು ವಿವರಿಸಿ.",
        Phrase.READBACK: "ಈ ದೂರನ್ನು ದಾಖಲಿಸಲಾಗುವುದು: {title}. ವರ್ಗ {category}, ಆದ್ಯತೆ "
        "{priority}. ಇದು ಸರಿಯೇ? ದಯವಿಟ್ಟು ಹೌದು ಅಥವಾ ಇಲ್ಲ ಎಂದು ಹೇಳಿ.",
        Phrase.TICKET_LOGGED: "ನಿಮ್ಮ ದೂರನ್ನು ಅನುಮೋದನೆಗೆ ಕಳುಹಿಸಲಾಗಿದೆ. ಅನುಮೋದಕರು ಒಪ್ಪಿದ ನಂತರ "
        "ಟಿಕೆಟ್ ಸಂಖ್ಯೆಯನ್ನು SMS ಮೂಲಕ ಕಳುಹಿಸಲಾಗುವುದು.",
        Phrase.TICKET_CANCELLED: "ಸರಿ, ದೂರನ್ನು ದಾಖಲಿಸಲಾಗಿಲ್ಲ.",
        Phrase.CANNOT_LOG: "ಕ್ಷಮಿಸಿ, ನಿಮ್ಮ ಪರವಾಗಿ ದೂರು ದಾಖಲಿಸಲು ಸಾಧ್ಯವಿಲ್ಲ.",
        Phrase.NO_TICKETS: "ನಿಮ್ಮ ಯಾವುದೇ ದೂರುಗಳು ದಾಖಲಾಗಿಲ್ಲ.",
        Phrase.ANSWER_SENT: "ಪೂರ್ಣ ಉತ್ತರ ಮತ್ತು ಅದರ ಮೂಲಗಳನ್ನು SMS ಮೂಲಕ ಕಳುಹಿಸಲಾಗಿದೆ.",
        Phrase.HANDOVER: "ನಿಮ್ಮನ್ನು ಒಬ್ಬ ವ್ಯಕ್ತಿಗೆ ಸಂಪರ್ಕಿಸುತ್ತಿದ್ದೇನೆ. ದಯವಿಟ್ಟು ಕಾಯಿರಿ.",
        Phrase.GOODBYE: "ಧನ್ಯವಾದಗಳು. ನಮಸ್ಕಾರ.",
        Phrase.TOO_MANY: "ಒಂದೇ ಬಾರಿಗೆ ತುಂಬಾ ಪ್ರಶ್ನೆಗಳು. ದಯವಿಟ್ಟು ಒಂದು ನಿಮಿಷದ ನಂತರ ಮತ್ತೆ ಕೇಳಿ.",
    },
    "hi": {
        Phrase.CONSENT: "नमस्ते, यह लेखा है। यह कॉल एक स्वचालित सिस्टम संभालता है और इसका "
        "लिखित रिकॉर्ड रखा जाता है।",
        Phrase.ASK_ID: "कृपया अपना कर्मचारी नंबर बताइए।",
        Phrase.ID_NOT_UNDERSTOOD: "माफ़ कीजिए, समझ नहीं आया। कृपया अपने कर्मचारी नंबर के पाँच अंक बताइए।",
        Phrase.CODE_SENT: "अगर यह नंबर पंजीकृत है, तो HR में दर्ज फ़ोन पर एक कोड भेजा गया है। "
        "कृपया छह अंकों का कोड बताइए।",
        Phrase.CODE_NOT_UNDERSTOOD: "माफ़ कीजिए, समझ नहीं आया। कृपया छह अंक बताइए।",
        Phrase.WRONG_CODE: "यह कोड सही नहीं है। कृपया फिर से कोशिश कीजिए।",
        Phrase.VERIFIED: "धन्यवाद, आपकी पहचान हो गई है। अपना सवाल पूछिए, या समस्या दर्ज "
        'करने के लिए "शिकायत" कहिए।',
        Phrase.DESCRIBE_ISSUE: "कृपया समस्या बताइए।",
        Phrase.READBACK: "यह शिकायत दर्ज की जाएगी: {title}। श्रेणी {category}, प्राथमिकता "
        "{priority}। क्या यह सही है? कृपया हाँ या नहीं कहिए।",
        Phrase.TICKET_LOGGED: "आपकी शिकायत मंज़ूरी के लिए भेज दी गई है। मंज़ूरी मिलने पर टिकट "
        "नंबर SMS से भेजा जाएगा।",
        Phrase.TICKET_CANCELLED: "ठीक है, शिकायत दर्ज नहीं की गई।",
        Phrase.CANNOT_LOG: "माफ़ कीजिए, आपकी ओर से शिकायत दर्ज नहीं की जा सकती।",
        Phrase.NO_TICKETS: "आपकी कोई शिकायत दर्ज नहीं है।",
        Phrase.ANSWER_SENT: "पूरा जवाब और उसके स्रोत SMS से भेज दिए गए हैं।",
        Phrase.HANDOVER: "आपको एक व्यक्ति से जोड़ा जा रहा है। कृपया प्रतीक्षा कीजिए।",
        Phrase.GOODBYE: "धन्यवाद। नमस्ते।",
        Phrase.TOO_MANY: "एक साथ बहुत सारे सवाल हो गए। कृपया एक मिनट बाद फिर पूछिए।",
    },
}

_CATEGORY: dict[str, dict[TicketCategory, str]] = {
    "en": {c: c.value.upper() if len(c.value) == 2 else c.value for c in TicketCategory},
    "kn": {
        TicketCategory.FACILITIES: "ಸೌಲಭ್ಯಗಳು",
        TicketCategory.IT: "IT",
        TicketCategory.HR: "HR",
        TicketCategory.SAFETY: "ಸುರಕ್ಷತೆ",
    },
    "hi": {
        TicketCategory.FACILITIES: "सुविधाएँ",
        TicketCategory.IT: "IT",
        TicketCategory.HR: "HR",
        TicketCategory.SAFETY: "सुरक्षा",
    },
}

_STATUS: dict[str, dict[ProposalStatus, str]] = {
    "en": {
        ProposalStatus.PENDING: "waiting for approval",
        ProposalStatus.APPROVED: "approved, the ticket is being created",
        ProposalStatus.REJECTED: "not approved",
        ProposalStatus.EXECUTED: "ticket {reference} created",
        ProposalStatus.FAILED: "the ticket could not be created",
        ProposalStatus.EXPIRED: "expired without a decision",
    },
    "kn": {
        ProposalStatus.PENDING: "ಅನುಮೋದನೆಗೆ ಕಾಯುತ್ತಿದೆ",
        ProposalStatus.APPROVED: "ಅನುಮೋದಿಸಲಾಗಿದೆ, ಟಿಕೆಟ್ ರಚಿಸಲಾಗುತ್ತಿದೆ",
        ProposalStatus.REJECTED: "ಅನುಮೋದಿಸಲಾಗಿಲ್ಲ",
        ProposalStatus.EXECUTED: "ಟಿಕೆಟ್ {reference} ರಚಿಸಲಾಗಿದೆ",
        ProposalStatus.FAILED: "ಟಿಕೆಟ್ ರಚಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ",
        ProposalStatus.EXPIRED: "ನಿರ್ಧಾರವಿಲ್ಲದೆ ಅವಧಿ ಮುಗಿದಿದೆ",
    },
    "hi": {
        ProposalStatus.PENDING: "मंज़ूरी का इंतज़ार है",
        ProposalStatus.APPROVED: "मंज़ूर हो गई, टिकट बन रहा है",
        ProposalStatus.REJECTED: "मंज़ूर नहीं हुई",
        ProposalStatus.EXECUTED: "टिकट {reference} बन गया",
        ProposalStatus.FAILED: "टिकट नहीं बन सका",
        ProposalStatus.EXPIRED: "बिना फ़ैसले के समय-सीमा ख़त्म हो गई",
    },
}

LANGUAGES = tuple(_PHRASES)
CONSENT_ORDER = ("kn", "hi", "en")  # before the caller speaks, the notice plays in all three


def _lang(language: str) -> str:
    return language if language in _PHRASES else "en"


def say(phrase: Phrase, language: str) -> str:
    return _PHRASES[_lang(language)][phrase]


def readback(ticket: TicketInputs, language: str) -> str:
    lang = _lang(language)
    return _PHRASES[lang][Phrase.READBACK].format(
        title=ticket.title,
        category=_CATEGORY[lang][ticket.category],
        priority=ticket.priority.value,
    )


def ticket_status(title: str, status: ProposalStatus, reference: str | None, language: str) -> str:
    lang = _lang(language)
    described = _STATUS[lang][status].format(reference=reference or "")
    return f"{title}: {described}."

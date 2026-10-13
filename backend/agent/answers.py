import re

from .matching import contains, country_key
from .models import Answer, Profile, Resume


def question_key(value: str) -> str:
    return re.sub(r"[\W_]+", " ", value.casefold()).strip()


def demographic_question(question: str) -> bool:
    return bool(re.search(r"\bgender\b|\brace\b|\bethnic(?:ity)?\b|\bveteran\b|\bdisabilit(?:y|ies)\b|\bpronouns\b|\bsexual orientation\b", question_key(question)))


def resolve_answer(question: str, profile: Profile, resume: Resume | None = None, country: str = "") -> Answer | None:
    key = question_key(question)
    if re.search(r"\bsignature\b|\bsign here\b|\bi (?:certify|attest|declare|agree)\b|\bassessment\b|\bcoding test\b", key):
        return None
    context_sensitive = any(w in key for w in ["authorized", "authorised", "authorization", "authorisation", "sponsorship", "sponsor"])
    for stored, value in profile.saved_answers.items():
        if question_key(stored) == key and value.strip() and not context_sensitive and not narrative_question(question):
            return Answer(value=value, source="saved_answer:" + stored)
    if any(w in key for w in ["authorized", "authorised", "authorization", "authorisation", "sponsorship", "sponsor"]):
        mapping = profile.sponsorship if "sponsor" in key else profile.authorization
        if re.search(r"\bnot\b|\bwithout\b|\bunable\b", key):
            return None
        named = [(c, v) for c, v in mapping.items() if contains(question, c)]
        explicit = re.search(r"\b(?:in|for)\s+(?:the\s+)?([A-Za-z][A-Za-z '-]*?)(?:[?!,;.]|$)", question)
        if not named and explicit:
            named = [(c,v) for c,v in mapping.items() if country_key(c) == country_key(explicit[1])]
            if not named:
                return None
        if not named and country and not explicit:
            named = [(c, v) for c, v in mapping.items() if country_key(c) == country_key(country)]
        if len(named) == 1:
            return Answer(value="Yes" if named[0][1] else "No", source=f"profile:{named[0][0]}")
        return None
    if "years" in key and "experience" in key:
        matches = [(skill, years) for skill, years in profile.skill_years.items() if contains(question, skill)]
        if len(matches) == 1:
            return Answer(value=f"{matches[0][1]:g}", source="profile:skill_years:" + matches[0][0])
        return None
    relocation = relocation_answer(question, profile)
    if relocation is not None:
        return relocation
    education = education_answer(key, profile)
    if education is not None:
        return education
    from .profile_records import structured_answer
    structured = structured_answer(question, profile)
    if structured is not None:
        return structured
    fields = {
        "first name": profile.first_name, "given name": profile.first_name,
        "last name": profile.last_name, "family name": profile.last_name,
        "full name": f"{profile.first_name} {profile.last_name}".strip(),
        "name": f"{profile.first_name} {profile.last_name}".strip(),
        "email": profile.email, "email address": profile.email,
        "phone": profile.phone, "phone number": profile.phone, "mobile phone": profile.phone,
        "location": profile.location, "current location": profile.location,
        "city": profile.location, "country": profile.country,
        "linkedin": profile.linkedin_url, "linkedin profile": profile.linkedin_url,
        "linkedin profile url": profile.linkedin_url,
        "website": profile.portfolio_url, "portfolio": profile.portfolio_url,
        "portfolio url": profile.portfolio_url,
        "availability": profile.availability, "notice period": profile.availability,
        "salary expectation": profile.salary_expectation, "expected salary": profile.salary_expectation,
    }
    if key in fields and fields[key]:
        return Answer(value=fields[key], source="profile:" + key)
    return None


def narrative_question(question: str) -> bool:
    if demographic_question(question):
        return False
    key = question_key(question)
    return any(phrase in key for phrase in ["why do you", "why are you", "tell us about", "describe your", "cover letter", "what interests you", "what excites you"])


EDUCATION_FIELDS = {
    "school": "institution", "school name": "institution", "institution": "institution",
    "institution name": "institution", "university": "institution", "university name": "institution",
    "college": "institution", "college name": "institution", "degree": "degree",
    "degree type": "degree", "field of study": "field", "major": "field",
    "graduation year": "graduation", "year of graduation": "graduation",
}


def education_answer(key: str, profile: Profile) -> Answer | None:
    if key in {"education", "education history", "educational background"} and profile.education.strip():
        return Answer(value=profile.education.strip(), source="profile:education")
    field = EDUCATION_FIELDS.get(key)
    if not field:
        return None
    values = set()
    for line in profile.education.splitlines():
        label, separator, value = line.partition(":")
        if separator and EDUCATION_FIELDS.get(question_key(label)) == field and value.strip():
            values.add(value.strip())
    if len(values) == 1:
        return Answer(value=values.pop(), source="profile:education:" + field)
    return None


def relocation_answer(question: str, profile: Profile) -> Answer | None:
    key = question_key(question)
    match = re.fullmatch(r"(?:are you|would you be) (?:willing|able|open) to relocate(?: to (.+))?", key)
    if not match or profile.willing_to_relocate is None:
        return None
    destination = match[1]
    if profile.willing_to_relocate is False:
        return Answer(value="No", source="profile:willing_to_relocate")
    if destination and not any(country_key(destination) == country_key(c) for c in profile.relocation_countries):
        return None
    return Answer(value="Yes", source="profile:relocation_countries" if destination else "profile:willing_to_relocate")

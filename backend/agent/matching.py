import re

from .models import Job, MatchResult, Profile, Resume, ResumeChoice, Search
from .requirements import assess_requirements, extract_requirements, resume_requirement_match

ALIASES = {
    "react.js": "react", "reactjs": "react", "node.js": "node", "nodejs": "node",
    "next.js": "nextjs", "next js": "nextjs", "typescript": "typescript",
    "js": "javascript", "ts": "typescript", "postgres": "postgresql",
    "k8s": "kubernetes", "amazon web services": "aws",
}
COUNTRIES = {"usa": "united states", "us": "united states", "uk": "united kingdom", "uae": "united arab emirates"}
STOP = set("a an the and or of in for to with on at is as we you our your engineer engineering senior junior developer development experience years year job role software application seeking looking using work skills position strong team required have will are be this that it from knowledge ability proficiency excellent familiar".split())


def normalized(text: str, aliases: dict[str, str] | None = None) -> str:
    text = text.casefold().strip()
    mapping = ALIASES | {k.strip().casefold(): v.strip().casefold() for k, v in (aliases or {}).items() if k.strip() and v.strip()}
    def canonical(alias):
        visited = []
        while alias in mapping:
            if alias in visited:
                return min(visited[visited.index(alias):])
            visited.append(alias)
            alias = mapping[alias]
        return alias
    mapping = {alias: canonical(alias) for alias in mapping}
    # Replace original matches once, so overlapping aliases cannot cascade or
    # mutate another configuration's vocabulary.
    pattern = r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)) + r")(?!\w)"
    return re.sub(pattern, lambda m: mapping[m[0]], text)


def contains(text: str, phrase: str, aliases: dict[str, str] | None = None) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(normalized(phrase, aliases)) + r"(?!\w)", normalized(text, aliases)))


ROLE_ALIASES = {
    "rn": "registered nurse", "lpn": "licensed practical nurse", "lvn": "licensed vocational nurse",
    "cdl driver": "commercial driver", "truck driver": "commercial driver",
    "commercial truck driver": "commercial driver",
    "software developer": "software engineer",
    "front-end developer": "frontend engineer", "front end developer": "frontend engineer",
    "frontend developer": "frontend engineer", "front-end engineer": "frontend engineer",
    "front end engineer": "frontend engineer",
    "back-end developer": "backend engineer", "back end developer": "backend engineer",
    "backend developer": "backend engineer", "back-end engineer": "backend engineer",
    "back end engineer": "backend engineer",
}
LEVEL_WORDS = {"senior", "sr", "junior", "jr", "entry", "level", "mid", "intern", "internship", "lead", "principal", "staff", "director", "executive"}


def normalized_role(title: str, aliases: dict[str, str] | None = None) -> str:
    text = title.casefold().strip()
    pattern = r"(?<!\w)(?:" + "|".join(re.escape(key) for key in sorted(ROLE_ALIASES, key=len, reverse=True)) + r")(?!\w)"
    return normalized(re.sub(pattern, lambda match: ROLE_ALIASES[match[0]], text), aliases)


def interpreted_seniority(title: str) -> str:
    text = title.casefold().replace("-", " ")
    for pattern, level in [
        (r"\b(?:intern|internship)\b", "intern"),
        (r"\b(?:junior|jr|entry level)\b", "junior"),
        (r"\b(?:executive|chief|ceo|cto|cfo)\b", "executive"),
        (r"\bdirector\b", "director"),
        (r"\b(?:principal|staff|lead)\b", "lead"),
        (r"\b(?:senior|sr)\b", "senior"),
        (r"\b(?:mid level|intermediate)\b", "mid"),
        (r"\bassociate\b", "associate"),
    ]:
        if re.search(pattern, text):
            return level
    return ""


def role_fit(target: str, actual: str, aliases: dict[str, str] | None = None, *, check_seniority: bool = True) -> float:
    target_level, actual_level = interpreted_seniority(target), interpreted_seniority(actual)
    if check_seniority and target_level and actual_level and target_level != actual_level:
        return 0.0
    wanted = {token for token in re.findall(r"[\w+#]+", normalized_role(target, aliases)) if token not in LEVEL_WORDS}
    available = set(re.findall(r"[\w+#]+", normalized_role(actual, aliases)))
    return len(wanted & available) / max(1, len(wanted))


def country_key(value: str) -> str:
    value = value.strip().casefold()
    return COUNTRIES.get(value, value)


def match_job(job: Job, search: Search, profile: Profile, aliases: dict[str, str] | None = None) -> MatchResult:
    text = f"{job.title} {job.description}"
    reasons, unknown, evidence = [], [], []
    restrictions = re.findall(r"(?:restricted to|only (?:open|available) to|must be|need to be)\s+(?:(currently)\s+)?(?:based in|located in|residents? of|living in)\s+([A-Za-z][A-Za-z ,'-]*?)(?:[.;\n]|$)", job.description, re.I)
    restrictions += [("currently", country) for country in re.findall(r"\bmust\s+currently\s+be\s+(?:based|located|living)\s+in\s+([A-Za-z][A-Za-z ,'-]*?)(?:[.;\n]|$)", job.description, re.I)]
    restrictions += [(current, country) for current, country in re.findall(r"\bmust\s+(?:(currently)\s+)?(?:reside|live)\s+in\s+([A-Za-z][A-Za-z ,'-]*?)(?:[.;\n]|$)", job.description, re.I)]
    for current_residence, restriction in restrictions:
        eligible_countries = [v.strip() for v in re.split(r",|\bor\b|\band\b", restriction, flags=re.I) if v.strip()]
        if not profile.country:
            unknown.append("Your residence is needed to check: " + restriction)
        elif not any(country_key(profile.country) == country_key(c) for c in eligible_countries):
            relocating = not current_residence and profile.willing_to_relocate is True and any(country_key(c) == country_key(target) for c in eligible_countries for target in profile.relocation_countries) and relocation_supported(job.description)
            if relocating:
                evidence.append("Residency requirement checked using explicit relocation preference and job relocation support: " + restriction)
            else:
                reasons.append("Job description restricts residency to: " + restriction)
    if any(contains(text, k, aliases) for k in search.excluded_keywords):
        reasons.append("Excluded keyword present")
    if any(contains(job.company, c, aliases) for c in search.excluded_companies):
        reasons.append("Excluded company")
    if search.included_companies and not any(contains(job.company, c, aliases) for c in search.included_companies):
        reasons.append("Company is not in your included companies")
    checks = [(search.countries, job.country, "country"), (search.cities, job.city, "city"), (search.workplaces, job.workplace, "work arrangement"), (search.seniorities, job.seniority or interpreted_seniority(job.title), "seniority"), (search.employment_types, job.employment_type, "employment type")]
    for allowed, actual, label in checks:
        if allowed:
            if not actual:
                unknown.append(f"Missing {label}")
            elif not any((interpreted_seniority(v) or v.casefold()) == (interpreted_seniority(actual) or actual.casefold()) if label == "seniority" else country_key(v) == country_key(actual) for v in allowed):
                reasons.append(f"{label.capitalize()} does not match: {actual}")
    for skill in search.required_skills:
        if not contains(text, skill, aliases):
            reasons.append(f"Required skill missing: {skill}")
    if search.easy_apply_only and not job.easy_apply:
        reasons.append("Not an Easy Apply job")
    if job.posted_days is not None and job.posted_days > search.posted_within_days:
        reasons.append("Job is older than your posting age limit")
    if search.min_salary is not None or search.max_salary is not None:
        if job.salary_min is None and job.salary_max is None:
            unknown.append("Missing salary")
        elif job.salary_currency.upper() != search.salary_currency.upper() or job.salary_period != search.salary_period:
            unknown.append("Salary currency or pay period is incompatible")
        else:
            high = job.salary_max if job.salary_max is not None else job.salary_min
            low = job.salary_min if job.salary_min is not None else job.salary_max
            if search.min_salary is not None and high < search.min_salary:
                reasons.append("Salary below minimum")
            if search.max_salary is not None and low > search.max_salary:
                reasons.append("Salary above maximum")
    if search.require_authorization:
        auth = next((v for c, v in profile.authorization.items() if country_key(c) == country_key(job.country)), None)
        if auth is None:
            unknown.append("Work authorization is unknown for the job country")
        elif not auth and not (search.sponsorship_required and job.sponsorship_available is True):
            reasons.append("Work authorization does not match")
    if search.sponsorship_required and job.sponsorship_available is not True:
        (unknown if job.sponsorship_available is None else reasons).append("Sponsorship is not confirmed")
    job_level = interpreted_seniority(job.title) or interpreted_seniority(job.seniority)
    target_levels = [interpreted_seniority(title) for title in search.titles]
    if job_level and target_levels and all(target_levels) and job_level not in target_levels:
        reasons.append(f"Seniority does not match target titles: {job_level}")
    for assessment in assess_requirements(job.description, profile, aliases):
        message = f"Mandatory requirement {assessment.status}: {assessment.requirement.evidence} — {assessment.reason}"
        if assessment.status == "unmet":
            reasons.append(message)
        elif assessment.status == "unknown":
            unknown.append(message)
        else:
            evidence.append(message)
    title_fit = 1 if not search.titles else max((role_fit(title, job.title, aliases) for title in search.titles), default=0)
    preferred = search.preferred_skills or profile.skills
    skill_fit = 1 if not preferred else sum(contains(text, s, aliases) for s in preferred) / len(preferred)
    score = round(title_fit * 50 + skill_fit * 50)
    if score < search.min_score:
        reasons.append(f"Match score {score} is below {search.min_score}")
    failures = reasons + unknown
    return MatchResult(eligible=not failures, score=score, reasons=failures or evidence or ["All configured requirements match"], needs_review=bool(unknown) and not reasons and search.missing_information == "review")


def tokens(text: str, aliases: dict[str, str] | None = None) -> set[str]:
    return {t for t in re.findall(r"[\w+#]+", normalized(text, aliases)) if len(t) > 1 and t not in STOP}


def select_resume(job: Job, resumes: list[Resume], minimum: int = 20, aliases: dict[str, str] | None = None, profile: Profile | None = None) -> ResumeChoice | None:
    """Rank visible evidence with role fit first; priority only breaks score ties."""
    required_terms = tokens(job.description + " " + job.title, aliases)
    mandatory = extract_requirements(job.description)
    choices = []
    for resume in resumes:
        text = " ".join([resume.text, *resume.skills, *resume.roles])
        content = tokens(text, aliases)
        overlap = required_terms & content
        # Explicit CV role records take precedence over incidental role words in
        # prose (for example selling software to nurses is not a nursing role).
        candidate_roles = resume.roles or [resume.text]
        title_fit = max((role_fit(job.title, role, aliases, check_seniority=bool(resume.roles)) for role in candidate_roles), default=0)
        mandatory_matches = [item for item in mandatory if resume_requirement_match(item, text, aliases)]
        mandatory_fit = len(mandatory_matches) / max(1, len(mandatory))
        general_fit = len(overlap) / max(1, len(required_terms))
        role_weight, general_weight = (50, 20) if mandatory else (65, 35)
        score = round(role_weight * title_fit + (30 * mandatory_fit if mandatory else 0) + general_weight * general_fit)
        reasons = [f"Role/title fit: {title_fit:.0%} ({role_weight * title_fit:.1f} points)"]
        if mandatory:
            reasons.append(f"Mandatory qualification terms: {len(mandatory_matches)}/{len(mandatory)} ({30 * mandatory_fit:.1f} points): " + "; ".join(item.name or "total experience" for item in mandatory_matches))
        reasons.append(f"Job description overlap: {general_fit:.0%} ({general_weight * general_fit:.1f} points). Matching terms: " + ", ".join(sorted(overlap)))
        choices.append((score, resume.priority, resume.id, resume, reasons))
    if not choices:
        return None
    score, _, _, resume, reasons = max(choices, key=lambda item: item[:3])
    if score < minimum:
        return None
    if sum(candidate[0] == score for candidate in choices) > 1:
        reasons.append(f"Equal relevance scores resolved by CV priority ({resume.priority}), then stable identifier")
    if profile is not None and mandatory:
        for item in assess_requirements(job.description, profile, aliases):
            reasons.append(f"Profile mandatory check {item.status}: {item.requirement.evidence} — {item.reason}")
    return ResumeChoice(resume=resume, score=score, reasons=reasons)



def relocation_supported(description: str) -> bool:
    """Require an affirmative offer, never treating 'no relocation' as support."""
    for sentence in re.split(r"[.;\n]", description):
        if re.search(r"\b(?:no|not|without|unavailable|cannot|unable)\b", sentence, re.I):
            continue
        if re.search(r"\brelocation\s+(?:assistance|support|package)(?:\s+(?:is|will be))?\s+(?:provided|available|offered)\b|\b(?:provide|offer|provides|offers)\s+relocation\s+(?:assistance|support|package)\b", sentence, re.I):
            return True
    return False

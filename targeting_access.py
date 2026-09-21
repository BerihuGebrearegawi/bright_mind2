"""Shared server-side learning-mode/audience access checks for BMT content."""

def student_audience_set(user, entitlement=None):
    user = user or {}; entitlement = entitlement or {}; audiences=set()
    mode=str(user.get("learningMode") or "Regular").strip()
    if mode == "Distance": audiences.add("Distance")
    else:
        premium = bool(entitlement.get("premium"))
        expires = entitlement.get("expiresAt")
        # A server-issued paid entitlement always carries an expiry.  Treat a
        # malformed/client-authored premium entitlement with no expiry as
        # inactive instead of granting permanent Paid Regular access.
        if "premium" in entitlement and premium and expires is None:
            premium = False
        if expires is not None:
            try:
                from datetime import datetime, timezone
                if isinstance(expires, str):
                    expires = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                if isinstance(expires, datetime):
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    premium = premium and expires > datetime.now(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                premium = False
        elif not premium:
            premium = bool(user.get("isPaid"))
        audiences.add("Paid Regular" if premium else "Free Regular")
    flags=user.get("audiences") or user.get("targetAudiences") or []
    if isinstance(flags,(list,tuple,set)): audiences.update(str(x).strip() for x in flags if str(x).strip() in {"Scholarship","Competition"})
    if user.get("scholarshipEligible") is True or user.get("isScholarship") is True: audiences.add("Scholarship")
    if user.get("competitionEligible") is True or user.get("isCompetitor") is True: audiences.add("Competition")
    return audiences

def content_target_allowed(user, content, entitlement=None):
    content=content or {}; modes=content.get("learningModes") or ["Regular"]; audiences=content.get("audiences") or ["Free Regular","Paid Regular"]
    mode=str(user.get("learningMode") or "Regular").strip()
    return mode in {str(x).strip() for x in modes} and bool(student_audience_set(user,entitlement) & {str(x).strip() for x in audiences})

def content_visible_or_untargeted(user, content, entitlement=None):
    """V31.108 Phase D: same rule as content_target_allowed(), but content
    with NO explicit learningModes/audiences set stays visible to everyone,
    Distance included.

    content_target_allowed() defaults an untagged item to ["Regular"] /
    ["Free Regular","Paid Regular"] - correct for Question Bank/Challenge
    content, which has been explicitly tagged since the targeting feature
    was introduced. It is the wrong default for the pre-existing Digital
    Library / Videos catalog: hundreds of untagged legacy records would
    silently vanish for every Distance student. Use this helper for those
    legacy libraries; keep using content_target_allowed() for content that
    is targeted from creation (Question Bank, Challenges, Distance Courses).
    """
    content = content or {}
    if not content.get("learningModes") and not content.get("audiences"):
        return True
    return content_target_allowed(user, content, entitlement)

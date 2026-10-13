"""Bounded mapping of semantic repeating sections to confirmed applicant records."""
import re

from .profile_records import record_answer

GROUPS = 'fieldset,[data-section],[data-automation-id="workExperience"],[data-automation-id="education"],[data-testid="employment-entry"],[data-testid="education-entry"]'
GROUP_INFO = '''el => {
 const title=el.getAttribute('data-section') || el.getAttribute('data-automation-id') || el.getAttribute('data-testid') || el.querySelector(':scope > legend')?.textContent || '';
 const section=/employment|work.?experience/i.test(title)?'employment':/education|school/i.test(title)?'education':'';
 return {section,title};
}'''


async def section_groups(scope, section):
    result=[]
    groups=scope.locator(GROUPS)
    for i in range(await groups.count()):
        group=groups.nth(i)
        if await group.is_visible() and (await group.evaluate(GROUP_INFO))['section']==section:
            result.append(group)
    leaves=[]
    for group in result:
        contains_record=False
        for other in result:
            handle=await other.element_handle()
            if await group.evaluate('(parent,child)=>parent!==child && parent.contains(child)',handle):
                contains_record=True
                break
        if not contains_record:leaves.append(group)
    return leaves


async def record_context(scope, element, profile):
    records=getattr(profile,'records',None)
    if records is None:return None
    for section in ('employment','education'):
        for index,group in enumerate(await section_groups(scope,section)):
            handle=await element.element_handle()
            if await group.evaluate('(group,el)=>group.contains(el)',handle):
                items=getattr(records,section)
                return (section,index,items[index] if index<len(items) else None)
    return None


async def ensure_repeating_groups(scope, profile, can_act):
    from .forms import act
    records=getattr(profile,'records',None)
    if records is None:return
    for section in ('employment','education'):
        target=min(len(getattr(records,section)),10)
        groups=await section_groups(scope,section)
        if not groups:continue
        pattern=r'^Add(?: another)? (?:employment|work experience)(?: record)?$' if section=='employment' else r'^Add(?: another)? (?:education|school)(?: record)?$'
        for _ in range(max(0,target-len(groups))):
            button=scope.get_by_role('button',name=re.compile(pattern,re.I))
            if await button.count()!=1 or not await button.is_visible():break
            before=len(await section_groups(scope,section))
            await act(can_act,button.click)
            if len(await section_groups(scope,section))<=before:break


def contextual_answer(context, label):
    if context is None:return None
    section,_,record=context
    return record_answer(section,record,label) if record is not None else None

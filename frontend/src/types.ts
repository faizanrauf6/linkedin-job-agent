export interface Profile {
  first_name:string; last_name:string; email:string; phone:string; location:string; country:string;
  linkedin_url:string; portfolio_url:string; summary:string; skills:string[]; skill_years:Record<string,number>;
  work_history:string; education:string; availability:string; salary_expectation:string;
  authorization:Record<string,boolean>; sponsorship:Record<string,boolean>; saved_answers:Record<string,string>;
  demographic_preference:'skip'|'decline';
  willing_to_relocate:boolean|null; relocation_countries:string[];
}
export interface Search {
  id:string; name:string; enabled:boolean; titles:string[]; excluded_keywords:string[]; required_skills:string[];
  preferred_skills:string[]; countries:string[]; cities:string[]; workplaces:('remote'|'hybrid'|'onsite')[];
  seniorities:string[]; employment_types:string[]; excluded_companies:string[]; included_companies:string[];
  min_salary:number|null; max_salary:number|null; salary_currency:string; salary_period:'year'|'month'|'hour';
  posted_within_days:number; easy_apply_only:boolean; require_authorization:boolean; sponsorship_required:boolean;
  missing_information:'review'|'reject'; min_score:number;
}
export interface Settings {
  mode:'review'|'automatic'; paused:boolean; interval_minutes:number; max_jobs_per_run:number; daily_limit:number;
  cv_min_score:number; timezone:string; operating_start:string; operating_end:string; trusted_employers:Record<string,string>;
  model_enabled:boolean; model_base_url:string; model_name:string; allow_cloud_model:boolean; allow_registration:boolean;
  max_search_pages:number; retry_attempts:number; retry_backoff_seconds:number; portal_cooldown_minutes:number;
  technology_aliases:Record<string,string>; registration_hosts:string[]; sso_hosts:Record<string,string[]>; prevent_sleep:boolean;
}
export interface Config {profile:Profile;searches:Search[];settings:Settings;revision:number}
export interface Resume {id:string;label:string;filename:string;text:string;skills:string[];roles:string[];priority:number;confirmed:boolean;sha256:string;created_at:string;identity_facts:Record<string,string>;conflicts:string[];conflict_acknowledged:boolean;conflict_fingerprint:string}
export interface Job {
  id:string;linkedin_id:string;title:string;company:string;description:string;url:string;application_url:string;
  country:string;city:string;location:string;workplace:string;status:string;score:number;reasons:string[];evidence:string[];
  resume_id:string;answers:Record<string,string>;answer_sources:Record<string,string>;questions:string[];error:string;created_at:string;
  easy_apply:boolean;submission_started:boolean;possible_duplicate:string;
  adapter_name:string;adapter_version:string;retry_at:string;poster_name:string;poster_url:string;verified_poster:boolean;risk_flags:string[];
}
export interface Status {paused:boolean;mode:string;busy:boolean;message:string;browser:string;counts:Record<string,number>;daily_attempts:number;daily_limit:number;last_discovery:string;next_discovery:string;browser_issue:string;setup:Record<string,boolean>}
export interface Event {id:string;created_at:string;level:string;message:string;job_id:string}
export type View='overview'|'jobs'|'profile'|'resumes'|'searches'|'settings';
export type Run=(work:()=>Promise<void>)=>void;

export const newSearch=():Search=>({id:crypto.randomUUID().replaceAll('-',''),name:'My search',enabled:true,titles:[],excluded_keywords:[],required_skills:[],preferred_skills:[],countries:[],cities:[],workplaces:['remote'],seniorities:[],employment_types:[],excluded_companies:[],included_companies:[],min_salary:null,max_salary:null,salary_currency:'USD',salary_period:'year',posted_within_days:7,easy_apply_only:false,require_authorization:false,sponsorship_required:false,missing_information:'review',min_score:55});

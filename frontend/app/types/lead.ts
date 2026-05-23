// Shared Lead shape — exported from one place so page.tsx and LeadTable
// agree on the type (TypeScript treats two identically-named interfaces
// in different files as distinct, which breaks callback variance when
// they're passed across the file boundary).
//
// Keep this surface narrow: only fields actually rendered or mutated by
// frontend code. Backend can return additional columns; TS is happy to
// silently ignore them at JSON.parse-time.

export interface Lead {
  id?: string;
  unique_key: string;
  company_name?: string;
  name?: string;
  website?: string;
  audit_status?: string;
  retry_count: number;
  audit_results?: {
    score: number;
    high_risk_flag?: boolean;
  };
  high_risk_flag?: boolean;
  facebook?: string;
  instagram?: string;
  linkedin?: string;
  tiktok?: string;
  pinterest?: string;
  company_size?: string;
  target_clients?: string;
  business_details?: string;
  leadership_team?: string;
  key_offerings?: string;
  last_error?: string;
  outreach_score?: number;
  phone?: string;
  segment?: string;
  linkedin_hook?: string;
  email_hook?: string;
  email?: string;
  pain_points?: string;
  seo_score?: number;
  created_at?: string;
  is_demo?: boolean;
  lead_source?: string;
}

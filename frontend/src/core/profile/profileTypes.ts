export interface MeResponse {
  user_id: string;
  tenant_id: string;
  role: string;
  roles: string[];
  deployment_mode: string;
  capabilities: string[];
  ui_profile: string;
}

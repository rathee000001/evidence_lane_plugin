import backendContract from "./public-backend-readiness.v1.json" with { type: "json" };

export const publicBackendContract = backendContract;

export function publicBackendReadiness(environment: NodeJS.ProcessEnv = process.env) {
  return {
    ...backendContract,
    runtime_configuration: {
      github_client_id_configured: Boolean(environment.EVIDENCE_LANE_GITHUB_APP_CLIENT_ID),
      github_client_secret_configured: Boolean(environment.EVIDENCE_LANE_GITHUB_APP_CLIENT_SECRET),
      github_webhook_secret_configured: Boolean(environment.EVIDENCE_LANE_GITHUB_APP_WEBHOOK_SECRET),
      github_session_secret_configured: Boolean(environment.EVIDENCE_LANE_GITHUB_APP_SESSION_SECRET),
      public_provider_proxy_present: false,
      secret_values_returned: false,
    },
  };
}

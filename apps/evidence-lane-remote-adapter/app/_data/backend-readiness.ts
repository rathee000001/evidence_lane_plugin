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
      optional_general_provider_enabled: environment.EVIDENCE_LANE_GENERAL_AI_ENABLED === "true",
      optional_general_provider_key_configured: Boolean(environment.OPENROUTER_API_KEY),
      secret_values_returned: false,
    },
  };
}

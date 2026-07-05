import { ApiResponseHandler } from "./api-error-handler";
import { getHeader } from "./header";
import { getApiUrl } from "@/utils/api";

export interface AdminUser {
  id: string;
  username: string;
  email: string | null;
  display_name: string | null;
  auth_provider: string;
  is_admin: boolean;
}

export interface TeamMember {
  membership_id: string;
  role: string;
  user: AdminUser;
}

export interface TeamRecord {
  id: string;
  name: string;
  slug: string;
  created_at: string;
  members: TeamMember[];
  member_count: number;
}

export interface IntegrationDefaultTeam {
  id: string;
  name: string;
  slug: string;
}

export interface IntegrationClientRecord {
  id: string;
  client_id: string;
  name: string;
  key_prefix: string;
  is_active: boolean;
  auto_provision_users: boolean;
  created_at: string;
  last_used_at: string | null;
  default_team: IntegrationDefaultTeam | null;
}

export interface IntegrationKeyResponse {
  client: IntegrationClientRecord;
  api_key: string;
}

export class AdminApi {
  static async getTeams(): Promise<TeamRecord[]> {
    const response = await fetch(getApiUrl("/api/v1/admin/teams"), {
      method: "GET",
      credentials: "include",
    });
    return ApiResponseHandler.handleResponse(response, "Failed to load teams");
  }

  static async createTeam(payload: {
    name: string;
    slug?: string;
  }): Promise<TeamRecord> {
    const response = await fetch(getApiUrl("/api/v1/admin/teams"), {
      method: "POST",
      credentials: "include",
      headers: getHeader(),
      body: JSON.stringify(payload),
    });
    return ApiResponseHandler.handleResponse(response, "Failed to create team");
  }

  static async updateTeam(
    teamId: string,
    payload: { name: string; slug?: string }
  ): Promise<TeamRecord> {
    const response = await fetch(getApiUrl(`/api/v1/admin/teams/${teamId}`), {
      method: "PATCH",
      credentials: "include",
      headers: getHeader(),
      body: JSON.stringify(payload),
    });
    return ApiResponseHandler.handleResponse(response, "Failed to update team");
  }

  static async deleteTeam(teamId: string): Promise<{ success: boolean }> {
    const response = await fetch(getApiUrl(`/api/v1/admin/teams/${teamId}`), {
      method: "DELETE",
      credentials: "include",
    });
    return ApiResponseHandler.handleResponse(response, "Failed to delete team");
  }

  static async getUsers(query = ""): Promise<AdminUser[]> {
    const params = new URLSearchParams();
    if (query.trim()) {
      params.set("query", query.trim());
    }
    const suffix = params.toString() ? `?${params.toString()}` : "";
    const response = await fetch(getApiUrl(`/api/v1/admin/users${suffix}`), {
      method: "GET",
      credentials: "include",
    });
    return ApiResponseHandler.handleResponse(response, "Failed to load users");
  }

  static async addTeamMember(
    teamId: string,
    payload: { user_id?: string; username?: string; email?: string; role?: string }
  ): Promise<TeamMember> {
    const response = await fetch(
      getApiUrl(`/api/v1/admin/teams/${teamId}/members`),
      {
        method: "POST",
        credentials: "include",
        headers: getHeader(),
        body: JSON.stringify(payload),
      }
    );
    return ApiResponseHandler.handleResponse(response, "Failed to add team member");
  }

  static async removeTeamMember(
    teamId: string,
    membershipId: string
  ): Promise<{ success: boolean }> {
    const response = await fetch(
      getApiUrl(`/api/v1/admin/teams/${teamId}/members/${membershipId}`),
      {
        method: "DELETE",
        credentials: "include",
      }
    );
    return ApiResponseHandler.handleResponse(
      response,
      "Failed to remove team member"
    );
  }

  static async getIntegrations(): Promise<IntegrationClientRecord[]> {
    const response = await fetch(getApiUrl("/api/v1/admin/integrations"), {
      method: "GET",
      credentials: "include",
    });
    return ApiResponseHandler.handleResponse(
      response,
      "Failed to load integrations"
    );
  }

  static async createIntegration(payload: {
    name: string;
    default_team_id?: string;
    auto_provision_users?: boolean;
  }): Promise<IntegrationKeyResponse> {
    const response = await fetch(getApiUrl("/api/v1/admin/integrations"), {
      method: "POST",
      credentials: "include",
      headers: getHeader(),
      body: JSON.stringify(payload),
    });
    return ApiResponseHandler.handleResponse(
      response,
      "Failed to create integration"
    );
  }

  static async updateIntegration(
    integrationId: string,
    payload: {
      name: string;
      default_team_id?: string;
      auto_provision_users?: boolean;
      is_active?: boolean;
    }
  ): Promise<IntegrationClientRecord> {
    const response = await fetch(
      getApiUrl(`/api/v1/admin/integrations/${integrationId}`),
      {
        method: "PATCH",
        credentials: "include",
        headers: getHeader(),
        body: JSON.stringify(payload),
      }
    );
    return ApiResponseHandler.handleResponse(
      response,
      "Failed to update integration"
    );
  }

  static async rotateIntegrationKey(
    integrationId: string
  ): Promise<IntegrationKeyResponse> {
    const response = await fetch(
      getApiUrl(`/api/v1/admin/integrations/${integrationId}/rotate-key`),
      {
        method: "POST",
        credentials: "include",
      }
    );
    return ApiResponseHandler.handleResponse(
      response,
      "Failed to rotate integration key"
    );
  }

  static async deleteIntegration(
    integrationId: string
  ): Promise<{ success: boolean }> {
    const response = await fetch(
      getApiUrl(`/api/v1/admin/integrations/${integrationId}`),
      {
        method: "DELETE",
        credentials: "include",
      }
    );
    return ApiResponseHandler.handleResponse(
      response,
      "Failed to delete integration"
    );
  }
}

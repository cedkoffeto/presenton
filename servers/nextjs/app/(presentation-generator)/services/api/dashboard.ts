import {
  getHeader,
} from "@/app/(presentation-generator)/services/api/header";
import { ApiResponseHandler } from "@/app/(presentation-generator)/services/api/api-error-handler";
import { getApiUrl } from "@/utils/api";

export interface PresentationResponse {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  data: any | null;
  file: string;
  n_slides: number;
  prompt: string;
  summary: string | null;
  theme: Record<string, any> | null;
  titles: string[];
  user_id: string;
  vector_store: any;

  thumbnail: string;
  slides: any[];
  visibility?: "private" | "team" | "public";
  team_id?: string | null;
  can_edit_access?: boolean;
  public_share_url?: string | null;
}

export class DashboardApi {

  static async getPresentations(): Promise<PresentationResponse[]> {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/all`),
        {
          method: "GET",
        }
      );

      // Handle the special case where 404 means "no presentations found"
      if (response.status === 404) {
        console.log("No presentations found");
        return [];
      }

      return await ApiResponseHandler.handleResponse(response, "Failed to fetch presentations");
    } catch (error) {
      console.error("Error fetching presentations:", error);
      throw error;
    }
  }

  static async getPresentation(id: string) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/${id}`),
        {
          method: "GET",
          credentials: "include",
        }
      );

      return await ApiResponseHandler.handleResponse(response, "Presentation not found");
    } catch (error) {
      console.error("Error fetching presentation:", error);
      throw error;
    }
  }

  static async deletePresentation(presentation_id: string) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/${presentation_id}`),
        {
          method: "DELETE",
          headers: getHeader(),
        }
      );

      return await ApiResponseHandler.handleResponseWithResult(response, "Failed to delete presentation");
    } catch (error) {
      console.error("Error deleting presentation:", error);
      return {
        success: false,
        message: error instanceof Error ? error.message : "Failed to delete presentation",
      };
    }
  }

  static async updatePresentationAccess(
    presentationId: string,
    visibility: "private" | "team" | "public"
  ): Promise<PresentationResponse> {
    const response = await fetch(
      getApiUrl(`/api/v1/ppt/presentation/${presentationId}/access`),
      {
        method: "PATCH",
        headers: getHeader(),
        body: JSON.stringify({ visibility }),
        credentials: "include",
      }
    );

    return await ApiResponseHandler.handleResponse(
      response,
      "Failed to update presentation access"
    );
  }
}

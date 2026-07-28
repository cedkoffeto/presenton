import { getApiUrl } from "@/utils/api";
import { ApiResponseHandler } from "./api-error-handler";
import { getHeader } from "./header";

export interface CloneTemplatePayload {
    id: string;
    name?: string;
    description?: string;
}

export interface CloneLayoutPayload {
    template_id: string;
    layout_id: string;
    layout_name?: string;
}

export type TemplateVisibility = "private" | "team" | "public";

class TemplateService {

    static async getCustomTemplateSummaries() {
        try {
            const response = await fetch(getApiUrl(`/api/v1/ppt/template/all`),);
            return await ApiResponseHandler.handleResponse(response, "Failed to get custom template summaries");
        } catch (error) {
            console.error("Failed to get custom template summaries", error);
            throw error;
        }
    }

    static async getCustomTemplateDetails(templateId: string) {
        try {
            const response = await fetch(getApiUrl(`/api/v1/ppt/template/${templateId}/layouts`),);
            return await ApiResponseHandler.handleResponse(response, "Failed to get custom template details");
        } catch (error) {
            console.error("Failed to get custom template details", error);
            throw error;
        }
    }

    static async deleteCustomTemplate(presentationId: string) {
        try {
            const response = await fetch(getApiUrl(`/api/v1/ppt/template-management/delete-templates/${presentationId}`), { method: "DELETE", headers: getHeader() });
            return await ApiResponseHandler.handleResponseWithResult(response, "Failed to delete custom template");
        } catch (error) {
            console.error("Failed to delete custom template", error);
            throw error;
        }
    }

    static async updateCustomTemplateAccess(templateId: string, visibility: TemplateVisibility) {
        try {
            const cleanedId = templateId.startsWith("custom-") ? templateId.slice("custom-".length) : templateId;
            const response = await fetch(getApiUrl(`/api/v1/ppt/template/${cleanedId}/access`), {
                method: "PATCH",
                headers: getHeader(),
                body: JSON.stringify({ visibility }),
            });
            return await ApiResponseHandler.handleResponse(response, "Failed to update template access");
        } catch (error) {
            console.error("Failed to update template access", error);
            throw error;
        }
    }

    static async cloneCustomTemplate(payload: CloneTemplatePayload) {
        try {
            const response = await fetch(getApiUrl(`/api/v1/ppt/template/clone`), {
                method: "POST",
                headers: getHeader(),
                body: JSON.stringify(payload),
            });
            return await ApiResponseHandler.handleResponse(response, "Failed to clone template");
        } catch (error) {
            console.error("Failed to clone template", error);
            throw error;
        }
    }

    static async cloneTemplateLayout(payload: CloneLayoutPayload) {
        try {
            const response = await fetch(getApiUrl(`/api/v1/ppt/template/slide-layout/clone`), {
                method: "POST",
                headers: getHeader(),
                body: JSON.stringify(payload),
            });
            return await ApiResponseHandler.handleResponse(response, "Failed to clone layout");
        } catch (error) {
            console.error("Failed to clone layout", error);
            throw error;
        }
    }
}

export default TemplateService;

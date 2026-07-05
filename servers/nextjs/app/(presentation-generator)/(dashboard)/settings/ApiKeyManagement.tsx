"use client";

import { notify } from "@/components/ui/sonner";
import {
  AdminApi,
  IntegrationClientRecord,
  TeamRecord,
} from "@/app/(presentation-generator)/services/api/admin";
import { Copy, KeyRound, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

type ApiKeyManagementProps = {
  enabled: boolean;
};

type IntegrationDrafts = Record<
  string,
  {
    name: string;
    defaultTeamId: string;
    autoProvisionUsers: boolean;
    isActive: boolean;
  }
>;

const ApiKeyManagement = ({ enabled }: ApiKeyManagementProps) => {
  const [integrations, setIntegrations] = useState<IntegrationClientRecord[]>([]);
  const [teams, setTeams] = useState<TeamRecord[]>([]);
  const [drafts, setDrafts] = useState<IntegrationDrafts>({});
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDefaultTeamId, setNewDefaultTeamId] = useState("");
  const [newAutoProvisionUsers, setNewAutoProvisionUsers] = useState(true);
  const [revealedKey, setRevealedKey] = useState<string | null>(null);

  const loadData = async () => {
    if (!enabled) {
      return;
    }

    setIsLoading(true);
    try {
      const [integrationsPayload, teamsPayload] = await Promise.all([
        AdminApi.getIntegrations(),
        AdminApi.getTeams(),
      ]);
      setIntegrations(integrationsPayload);
      setTeams(teamsPayload);
      setDrafts(
        Object.fromEntries(
          integrationsPayload.map((integration) => [
            integration.id,
            {
              name: integration.name,
              defaultTeamId: integration.default_team?.id || "",
              autoProvisionUsers: integration.auto_provision_users,
              isActive: integration.is_active,
            },
          ])
        )
      );
    } catch (error) {
      notify.error(
        "Could not load API keys",
        error instanceof Error ? error.message : "Please try again."
      );
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, [enabled]);

  const copyKey = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      notify.success("API key copied", "Store it in your integration securely.");
    } catch {
      notify.error("Copy failed", "Copy the key manually before closing this message.");
    }
  };

  const handleCreateIntegration = async () => {
    if (!newName.trim()) {
      notify.warning("Name required", "Add an integration name before creating a key.");
      return;
    }

    setIsSubmitting(true);
    try {
      const payload = await AdminApi.createIntegration({
        name: newName.trim(),
        default_team_id: newDefaultTeamId || undefined,
        auto_provision_users: newAutoProvisionUsers,
      });
      setNewName("");
      setNewDefaultTeamId("");
      setNewAutoProvisionUsers(true);
      setRevealedKey(payload.api_key);
      await loadData();
      notify.success("API key created", "Copy the key now. It will not be shown again.");
    } catch (error) {
      notify.error(
        "Could not create API key",
        error instanceof Error ? error.message : "Please try again."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleUpdateIntegration = async (integrationId: string) => {
    const draft = drafts[integrationId];
    if (!draft?.name.trim()) {
      notify.warning("Name required", "Add a name before saving integration changes.");
      return;
    }

    setIsSubmitting(true);
    try {
      const updated = await AdminApi.updateIntegration(integrationId, {
        name: draft.name.trim(),
        default_team_id: draft.defaultTeamId || undefined,
        auto_provision_users: draft.autoProvisionUsers,
        is_active: draft.isActive,
      });
      setIntegrations((current) =>
        current.map((item) => (item.id === integrationId ? updated : item))
      );
      notify.success("Integration updated", "The API key settings have been saved.");
    } catch (error) {
      notify.error(
        "Could not update integration",
        error instanceof Error ? error.message : "Please try again."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRotateKey = async (integrationId: string) => {
    setIsSubmitting(true);
    try {
      const payload = await AdminApi.rotateIntegrationKey(integrationId);
      setRevealedKey(payload.api_key);
      setIntegrations((current) =>
        current.map((item) => (item.id === integrationId ? payload.client : item))
      );
      notify.success("API key rotated", "Copy the new key now. The previous one no longer works.");
    } catch (error) {
      notify.error(
        "Could not rotate API key",
        error instanceof Error ? error.message : "Please try again."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteIntegration = async (integrationId: string) => {
    setIsSubmitting(true);
    try {
      await AdminApi.deleteIntegration(integrationId);
      setIntegrations((current) => current.filter((item) => item.id !== integrationId));
      notify.success("Integration removed", "The API key has been revoked and deleted.");
    } catch (error) {
      notify.error(
        "Could not delete integration",
        error instanceof Error ? error.message : "Please try again."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!enabled) {
    return null;
  }

  if (isLoading) {
    return (
      <div className="w-full rounded-[20px] bg-[#F9F8F8] p-7">
        <div className="flex min-h-[220px] items-center justify-center rounded-[16px] border border-[#EDEEEF] bg-white">
          <Loader2 className="h-5 w-5 animate-spin text-[#5146E5]" />
        </div>
      </div>
    );
  }

  return (
    <div className="w-full space-y-6">
      <div className="rounded-[20px] bg-[#F9F8F8] p-7">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div className="max-w-xl">
            <h4 className="font-unbounded text-lg font-normal text-black">Integration API keys</h4>
            <p className="mt-2 font-syne text-sm leading-relaxed text-[#494A4D]">
              Create one key per external system. Each integration can be disabled, rotated, or pinned to a default team without changing container environment variables.
            </p>
          </div>
          <div className="flex h-14 w-14 items-center justify-center rounded-[12px] bg-[#F4F3FF]">
            <KeyRound className="h-6 w-6 text-[#5146E5]" />
          </div>
        </div>

        {revealedKey ? (
          <div className="mb-5 rounded-[16px] border border-[#D9D6FE] bg-white p-5">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#7A5AF8]">
              New API key
            </p>
            <p className="mt-2 break-all rounded-[10px] bg-[#F9F8F8] px-4 py-3 font-mono text-xs text-[#191919]">
              {revealedKey}
            </p>
            <div className="mt-4 flex gap-3">
              <button
                type="button"
                onClick={() => void copyKey(revealedKey)}
                className="inline-flex items-center gap-2 rounded-[58px] border border-[#EDEEEF] bg-[#7C51F8] px-5 py-3 font-syne text-xs font-semibold text-white transition hover:bg-[#6d46e6]"
              >
                <Copy className="h-4 w-4" />
                Copy key
              </button>
              <button
                type="button"
                onClick={() => setRevealedKey(null)}
                className="inline-flex items-center gap-2 rounded-[58px] border border-[#EDEEEF] bg-white px-5 py-3 font-syne text-xs font-semibold text-[#191919] transition hover:bg-[#F9F8F8]"
              >
                Dismiss
              </button>
            </div>
          </div>
        ) : null}

        <div className="rounded-[16px] border border-[#EDEEEF] bg-white p-5">
          <div className="mb-4">
            <p className="text-sm font-semibold text-[#191919]">Create provider key</p>
            <p className="mt-1 text-xs leading-relaxed text-[#6B7280]">
              Use one key per connector such as OpenWebUI, an internal portal, or an automation service.
            </p>
          </div>
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px]">
            <input
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder="OpenWebUI production"
              className="h-11 rounded-[10px] border border-[#E1E1E5] bg-white px-4 text-sm text-[#191919] outline-none transition focus:border-[#D9D6FE] focus:ring-2 focus:ring-[#D9D6FE]"
            />
            <select
              value={newDefaultTeamId}
              onChange={(event) => setNewDefaultTeamId(event.target.value)}
              className="h-11 rounded-[10px] border border-[#E1E1E5] bg-white px-4 text-sm text-[#191919] outline-none transition focus:border-[#D9D6FE] focus:ring-2 focus:ring-[#D9D6FE]"
            >
              <option value="">No default team</option>
              {teams.map((team) => (
                <option key={team.id} value={team.id}>
                  {team.name}
                </option>
              ))}
            </select>
          </div>
          <label className="mt-4 flex items-center gap-3 text-sm text-[#191919]">
            <input
              type="checkbox"
              checked={newAutoProvisionUsers}
              onChange={(event) => setNewAutoProvisionUsers(event.target.checked)}
              className="h-4 w-4 rounded border border-[#D9D6FE] text-[#7C51F8] focus:ring-[#D9D6FE]"
            />
            Auto-create end users when the integration sends a new user context
          </label>
          <button
            type="button"
            onClick={handleCreateIntegration}
            disabled={isSubmitting}
            className="mt-4 inline-flex items-center gap-2 rounded-[58px] border border-[#EDEEEF] bg-[#7C51F8] px-5 py-3 font-syne text-xs font-semibold text-white transition hover:bg-[#6d46e6] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            Create API key
          </button>
        </div>
      </div>

      <div className="rounded-[20px] bg-[#F9F8F8] p-7">
        <div className="space-y-4">
          {integrations.length === 0 ? (
            <div className="rounded-[16px] border border-dashed border-[#D9D6FE] bg-white p-6 text-sm text-[#6B7280]">
              No integration keys yet.
            </div>
          ) : (
            integrations.map((integration) => (
              <div key={integration.id} className="rounded-[16px] border border-[#EDEEEF] bg-white p-5">
                <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                  <div>
                    <div className="flex items-center gap-3">
                      <h5 className="text-base font-semibold text-[#191919]">{integration.name}</h5>
                      <span className="rounded-full border border-[#EDEEEF] px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-[#7A5AF8]">
                        {integration.is_active ? "active" : "disabled"}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-[#6B7280]">
                      Prefix: {integration.key_prefix} · Last used: {integration.last_used_at || "never"}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-3">
                    <button
                      type="button"
                      onClick={() => void handleRotateKey(integration.id)}
                      disabled={isSubmitting}
                      className="inline-flex items-center gap-2 rounded-[58px] border border-[#EDEEEF] bg-white px-4 py-2.5 font-syne text-xs font-semibold text-[#191919] transition hover:bg-[#F9F8F8] disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <RefreshCw className="h-4 w-4" />
                      Rotate key
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleDeleteIntegration(integration.id)}
                      disabled={isSubmitting}
                      className="inline-flex items-center gap-2 rounded-[58px] border border-[#F0D5DD] bg-white px-4 py-2.5 font-syne text-xs font-semibold text-[#B42318] transition hover:bg-[#FFF5F5] disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <Trash2 className="h-4 w-4" />
                      Delete
                    </button>
                  </div>
                </div>

                <div className="mt-4 rounded-[12px] border border-[#EDEEEF] bg-[#F9F8F8] p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#7A5AF8]">
                    Integration settings
                  </p>
                  <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px]">
                    <input
                      value={drafts[integration.id]?.name || ""}
                      onChange={(event) =>
                        setDrafts((current) => ({
                          ...current,
                          [integration.id]: {
                            ...current[integration.id],
                            name: event.target.value,
                          },
                        }))
                      }
                      className="h-11 rounded-[10px] border border-[#E1E1E5] bg-white px-4 text-sm text-[#191919] outline-none transition focus:border-[#D9D6FE] focus:ring-2 focus:ring-[#D9D6FE]"
                    />
                    <select
                      value={drafts[integration.id]?.defaultTeamId || ""}
                      onChange={(event) =>
                        setDrafts((current) => ({
                          ...current,
                          [integration.id]: {
                            ...current[integration.id],
                            defaultTeamId: event.target.value,
                          },
                        }))
                      }
                      className="h-11 rounded-[10px] border border-[#E1E1E5] bg-white px-4 text-sm text-[#191919] outline-none transition focus:border-[#D9D6FE] focus:ring-2 focus:ring-[#D9D6FE]"
                    >
                      <option value="">No default team</option>
                      {teams.map((team) => (
                        <option key={team.id} value={team.id}>
                          {team.name}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="mt-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <label className="flex items-center gap-3 text-sm text-[#191919]">
                      <input
                        type="checkbox"
                        checked={drafts[integration.id]?.autoProvisionUsers ?? true}
                        onChange={(event) =>
                          setDrafts((current) => ({
                            ...current,
                            [integration.id]: {
                              ...current[integration.id],
                              autoProvisionUsers: event.target.checked,
                            },
                          }))
                        }
                        className="h-4 w-4 rounded border border-[#D9D6FE] text-[#7C51F8] focus:ring-[#D9D6FE]"
                      />
                      Auto-provision users
                    </label>
                    <label className="flex items-center gap-3 text-sm text-[#191919]">
                      <input
                        type="checkbox"
                        checked={drafts[integration.id]?.isActive ?? true}
                        onChange={(event) =>
                          setDrafts((current) => ({
                            ...current,
                            [integration.id]: {
                              ...current[integration.id],
                              isActive: event.target.checked,
                            },
                          }))
                        }
                        className="h-4 w-4 rounded border border-[#D9D6FE] text-[#7C51F8] focus:ring-[#D9D6FE]"
                      />
                      Integration enabled
                    </label>
                    <button
                      type="button"
                      onClick={() => void handleUpdateIntegration(integration.id)}
                      disabled={isSubmitting}
                      className="inline-flex items-center justify-center rounded-[58px] border border-[#EDEEEF] bg-white px-5 py-3 font-syne text-xs font-semibold text-[#191919] transition hover:bg-[#FFFFFF] disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      Save
                    </button>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};

export default ApiKeyManagement;

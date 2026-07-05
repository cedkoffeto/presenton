"use client";

import { notify } from "@/components/ui/sonner";
import {
  AdminApi,
  AdminUser,
  TeamRecord,
} from "@/app/(presentation-generator)/services/api/admin";
import { Loader2, Plus, Trash2, Users } from "lucide-react";
import { useEffect, useState } from "react";

type TeamManagementProps = {
  enabled: boolean;
};

const TeamManagement = ({ enabled }: TeamManagementProps) => {
  const [teams, setTeams] = useState<TeamRecord[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [teamDrafts, setTeamDrafts] = useState<Record<string, { name: string; slug: string }>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [teamName, setTeamName] = useState("");
  const [teamSlug, setTeamSlug] = useState("");
  const [selectedTeamId, setSelectedTeamId] = useState("");
  const [selectedUserId, setSelectedUserId] = useState("");

  const loadData = async () => {
    if (!enabled) {
      return;
    }

    setIsLoading(true);
    try {
      const [teamsPayload, usersPayload] = await Promise.all([
        AdminApi.getTeams(),
        AdminApi.getUsers(),
      ]);
      setTeams(teamsPayload);
      setUsers(usersPayload);
      setTeamDrafts(
        Object.fromEntries(
          teamsPayload.map((team) => [team.id, { name: team.name, slug: team.slug }])
        )
      );
      setSelectedTeamId((current) => current || teamsPayload[0]?.id || "");
    } catch (error) {
      notify.error(
        "Could not load teams",
        error instanceof Error ? error.message : "Please try again."
      );
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, [enabled]);

  const selectedTeam = teams.find((team) => team.id === selectedTeamId) || teams[0] || null;
  const assignableUsers = users.filter(
    (user) => !selectedTeam?.members.some((member) => member.user.id === user.id)
  );

  const handleCreateTeam = async () => {
    if (!teamName.trim()) {
      notify.warning("Team name required", "Add a team name before creating it.");
      return;
    }

    setIsSubmitting(true);
    try {
      const team = await AdminApi.createTeam({
        name: teamName.trim(),
        slug: teamSlug.trim() || undefined,
      });
      setTeams((current) => [...current, team].sort((a, b) => a.name.localeCompare(b.name)));
      setSelectedTeamId(team.id);
      setTeamName("");
      setTeamSlug("");
      notify.success("Team created", "The new team is ready for members.");
    } catch (error) {
      notify.error(
        "Could not create team",
        error instanceof Error ? error.message : "Please try again."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteTeam = async (teamId: string) => {
    setIsSubmitting(true);
    try {
      await AdminApi.deleteTeam(teamId);
      setTeams((current) => current.filter((team) => team.id !== teamId));
      setSelectedTeamId((current) => (current === teamId ? "" : current));
      notify.success("Team deleted", "The team has been removed.");
    } catch (error) {
      notify.error(
        "Could not delete team",
        error instanceof Error ? error.message : "Please try again."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleUpdateTeam = async (teamId: string) => {
    const draft = teamDrafts[teamId];
    if (!draft?.name.trim()) {
      notify.warning("Team name required", "Add a team name before saving changes.");
      return;
    }

    setIsSubmitting(true);
    try {
      const updated = await AdminApi.updateTeam(teamId, {
        name: draft.name.trim(),
        slug: draft.slug.trim() || undefined,
      });
      setTeams((current) =>
        current
          .map((team) =>
            team.id === teamId
              ? { ...team, name: updated.name, slug: updated.slug }
              : team
          )
          .sort((a, b) => a.name.localeCompare(b.name))
      );
      setTeamDrafts((current) => ({
        ...current,
        [teamId]: { name: updated.name, slug: updated.slug },
      }));
      notify.success("Team updated", "The team details have been saved.");
    } catch (error) {
      notify.error(
        "Could not update team",
        error instanceof Error ? error.message : "Please try again."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleAddMember = async () => {
    if (!selectedTeam?.id || !selectedUserId) {
      notify.warning("Member required", "Choose a user to add to this team.");
      return;
    }

    setIsSubmitting(true);
    try {
      await AdminApi.addTeamMember(selectedTeam.id, {
        user_id: selectedUserId,
      });
      setSelectedUserId("");
      await loadData();
      notify.success("Member added", "The user is now part of the team.");
    } catch (error) {
      notify.error(
        "Could not add member",
        error instanceof Error ? error.message : "Please try again."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRemoveMember = async (teamId: string, membershipId: string) => {
    setIsSubmitting(true);
    try {
      await AdminApi.removeTeamMember(teamId, membershipId);
      await loadData();
      notify.success("Member removed", "The user has been removed from the team.");
    } catch (error) {
      notify.error(
        "Could not remove member",
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
            <h4 className="font-unbounded text-lg font-normal text-black">Team management</h4>
            <p className="mt-2 font-syne text-sm leading-relaxed text-[#494A4D]">
              Teams stay admin-managed. Create shared workspaces, then attach existing users to the right team for controlled access to presentations, templates, and themes.
            </p>
          </div>
          <div className="flex h-14 w-14 items-center justify-center rounded-[12px] bg-[#F4F3FF]">
            <Users className="h-6 w-6 text-[#5146E5]" />
          </div>
        </div>

        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          <div className="rounded-[16px] border border-[#EDEEEF] bg-white p-5">
            <div className="mb-4">
              <p className="text-sm font-semibold text-[#191919]">Create a team</p>
              <p className="mt-1 text-xs leading-relaxed text-[#6B7280]">
                Teams are private until resources are explicitly shared to them.
              </p>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <input
                value={teamName}
                onChange={(event) => setTeamName(event.target.value)}
                placeholder="Product team"
                className="h-11 rounded-[10px] border border-[#E1E1E5] bg-white px-4 text-sm text-[#191919] outline-none transition focus:border-[#D9D6FE] focus:ring-2 focus:ring-[#D9D6FE]"
              />
              <input
                value={teamSlug}
                onChange={(event) => setTeamSlug(event.target.value)}
                placeholder="product-team"
                className="h-11 rounded-[10px] border border-[#E1E1E5] bg-white px-4 text-sm text-[#191919] outline-none transition focus:border-[#D9D6FE] focus:ring-2 focus:ring-[#D9D6FE]"
              />
            </div>
            <button
              type="button"
              onClick={handleCreateTeam}
              disabled={isSubmitting}
              className="mt-4 inline-flex items-center gap-2 rounded-[58px] border border-[#EDEEEF] bg-[#7C51F8] px-5 py-3 font-syne text-xs font-semibold text-white transition hover:bg-[#6d46e6] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
              Create team
            </button>
          </div>

          <div className="rounded-[16px] border border-[#EDEEEF] bg-white p-5">
            <div className="mb-4">
              <p className="text-sm font-semibold text-[#191919]">Add member</p>
              <p className="mt-1 text-xs leading-relaxed text-[#6B7280]">
                Users appear here once they sign in through local auth, OIDC, or a trusted integration flow.
              </p>
            </div>
            <div className="space-y-3">
              <select
                value={selectedTeam?.id || ""}
                onChange={(event) => setSelectedTeamId(event.target.value)}
                className="h-11 w-full rounded-[10px] border border-[#E1E1E5] bg-white px-4 text-sm text-[#191919] outline-none transition focus:border-[#D9D6FE] focus:ring-2 focus:ring-[#D9D6FE]"
              >
                <option value="">Choose a team</option>
                {teams.map((team) => (
                  <option key={team.id} value={team.id}>
                    {team.name}
                  </option>
                ))}
              </select>
              <select
                value={selectedUserId}
                onChange={(event) => setSelectedUserId(event.target.value)}
                className="h-11 w-full rounded-[10px] border border-[#E1E1E5] bg-white px-4 text-sm text-[#191919] outline-none transition focus:border-[#D9D6FE] focus:ring-2 focus:ring-[#D9D6FE]"
              >
                <option value="">Choose a user</option>
                {assignableUsers.map((user) => (
                  <option key={user.id} value={user.id}>
                    {user.display_name || user.username}
                    {user.email ? ` · ${user.email}` : ""}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              onClick={handleAddMember}
              disabled={isSubmitting || !selectedTeam?.id || !selectedUserId}
              className="mt-4 inline-flex items-center gap-2 rounded-[58px] border border-[#EDEEEF] bg-white px-5 py-3 font-syne text-xs font-semibold text-[#191919] transition hover:bg-[#F9F8F8] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
              Add to team
            </button>
          </div>
        </div>
      </div>

      <div className="rounded-[20px] bg-[#F9F8F8] p-7">
        <div className="space-y-4">
          {teams.length === 0 ? (
            <div className="rounded-[16px] border border-dashed border-[#D9D6FE] bg-white p-6 text-sm text-[#6B7280]">
              No teams yet. Create the first team to start assigning users.
            </div>
          ) : (
            teams.map((team) => (
              <div key={team.id} className="rounded-[16px] border border-[#EDEEEF] bg-white p-5">
                <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                  <div>
                    <div className="flex items-center gap-3">
                      <h5 className="text-base font-semibold text-[#191919]">{team.name}</h5>
                      <span className="rounded-full border border-[#EDEEEF] px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-[#7A5AF8]">
                        {team.member_count} member{team.member_count === 1 ? "" : "s"}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-[#6B7280]">Slug: {team.slug}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleDeleteTeam(team.id)}
                    disabled={isSubmitting}
                    className="inline-flex items-center gap-2 rounded-[58px] border border-[#F0D5DD] bg-white px-4 py-2.5 font-syne text-xs font-semibold text-[#B42318] transition hover:bg-[#FFF5F5] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <Trash2 className="h-4 w-4" />
                    Delete
                  </button>
                </div>

                <div className="mt-4 rounded-[12px] border border-[#EDEEEF] bg-[#F9F8F8] p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#7A5AF8]">
                    Team details
                  </p>
                  <div className="mt-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                    <input
                      value={teamDrafts[team.id]?.name || ""}
                      onChange={(event) =>
                        setTeamDrafts((current) => ({
                          ...current,
                          [team.id]: {
                            name: event.target.value,
                            slug: current[team.id]?.slug || team.slug,
                          },
                        }))
                      }
                      className="h-11 rounded-[10px] border border-[#E1E1E5] bg-white px-4 text-sm text-[#191919] outline-none transition focus:border-[#D9D6FE] focus:ring-2 focus:ring-[#D9D6FE]"
                    />
                    <input
                      value={teamDrafts[team.id]?.slug || ""}
                      onChange={(event) =>
                        setTeamDrafts((current) => ({
                          ...current,
                          [team.id]: {
                            name: current[team.id]?.name || team.name,
                            slug: event.target.value,
                          },
                        }))
                      }
                      className="h-11 rounded-[10px] border border-[#E1E1E5] bg-white px-4 text-sm text-[#191919] outline-none transition focus:border-[#D9D6FE] focus:ring-2 focus:ring-[#D9D6FE]"
                    />
                    <button
                      type="button"
                      onClick={() => void handleUpdateTeam(team.id)}
                      disabled={isSubmitting}
                      className="inline-flex items-center justify-center rounded-[58px] border border-[#EDEEEF] bg-white px-5 py-3 font-syne text-xs font-semibold text-[#191919] transition hover:bg-[#FFFFFF] disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      Save
                    </button>
                  </div>
                </div>

                <div className="mt-4 space-y-3">
                  {team.members.length === 0 ? (
                    <div className="rounded-[12px] border border-dashed border-[#EDEEEF] px-4 py-3 text-xs text-[#6B7280]">
                      No members yet.
                    </div>
                  ) : (
                    team.members.map((member) => (
                      <div
                        key={member.membership_id}
                        className="flex flex-col gap-3 rounded-[12px] border border-[#EDEEEF] px-4 py-3 md:flex-row md:items-center md:justify-between"
                      >
                        <div>
                          <p className="text-sm font-medium text-[#191919]">
                            {member.user.display_name || member.user.username}
                          </p>
                          <p className="mt-1 text-xs text-[#6B7280]">
                            {member.user.email || member.user.username} · {member.role}
                          </p>
                        </div>
                        <button
                          type="button"
                          onClick={() => void handleRemoveMember(team.id, member.membership_id)}
                          disabled={isSubmitting}
                          className="inline-flex items-center gap-2 self-start rounded-[58px] border border-[#EDEEEF] bg-white px-4 py-2.5 font-syne text-xs font-semibold text-[#191919] transition hover:bg-[#F9F8F8] disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          <Trash2 className="h-4 w-4" />
                          Remove
                        </button>
                      </div>
                    ))
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};

export default TeamManagement;

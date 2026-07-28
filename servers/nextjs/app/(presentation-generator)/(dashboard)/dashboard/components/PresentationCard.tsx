'use client'
import React, { useEffect } from "react";

import { Card } from "@/components/ui/card";
import { DashboardApi } from "@/app/(presentation-generator)/services/api/dashboard";
import { AlertTriangle, EllipsisVertical, Globe2, Loader2, Lock, Trash, Users } from "lucide-react";
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { usePathname, useRouter } from "next/navigation";
import { notify } from "@/components/ui/sonner";

import { useFontLoader } from "@/app/(presentation-generator)/hooks/useFontLoad";
import SlideScale from "@/app/(presentation-generator)/components/PresentationRender";
import MarkdownRenderer from "@/components/MarkDownRender";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";

type PresentationVisibility = "private" | "team" | "public";

const VISIBILITY_OPTIONS: Array<{
  value: PresentationVisibility;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  { value: "private", label: "Private", icon: Lock },
  { value: "team", label: "Team", icon: Users },
  { value: "public", label: "Public", icon: Globe2 },
];

const VisibilityBadge = ({ visibility }: { visibility: PresentationVisibility }) => {
  const option =
    VISIBILITY_OPTIONS.find((item) => item.value === visibility) ||
    VISIBILITY_OPTIONS[0];
  const Icon = option.icon;

  return (
    <span className="inline-flex h-7 items-center gap-1.5 rounded-full border border-[#E6E8EC] bg-white px-2.5 text-xs font-medium capitalize text-[#5B5F66]">
      <Icon className="h-3.5 w-3.5" />
      {option.label}
    </span>
  );
};

export const PresentationCard = ({
  id,
  title,
  presentation,
  onDeleted
}: {
  id: string;
  title: string;
  presentation: any;
  onDeleted?: (presentationId: string) => void;
}) => {
  const router = useRouter();
  const pathname = usePathname();
  const [showDeleteDialog, setShowDeleteDialog] = React.useState(false);
  const [isDeleting, setIsDeleting] = React.useState(false);
  const [visibility, setVisibility] = React.useState<PresentationVisibility>(
    presentation?.visibility || "private"
  );
  const [isUpdatingAccess, setIsUpdatingAccess] = React.useState(false);
  const canEditAccess = Boolean(presentation?.can_edit_access);

  useEffect(() => {
    setVisibility(presentation?.visibility || "private");
  }, [presentation?.visibility]);

  const handlePreview = (e: React.MouseEvent) => {
    e.preventDefault();
    trackEvent(MixpanelEvent.Dashboard_Presentation_Opened, {
      pathname,
      presentation_id: id,
      title_length: (title || "").length,
      slide_count: presentation?.slides?.length || 0,
    });
    router.push(`/presentation?id=${id}&type=standard`);
  };
  useEffect(() => {
    applyTheme(presentation.theme)
  }, [])
  const applyTheme = async (theme: any) => {
    const element = document.getElementById(`dashboard-presentation-card-${id}`)
    if (!element) return;

    if (!theme || !theme.data || !theme.data.colors['graph_0']) { return; }
    const cssVariables = {
      '--primary-color': theme.data.colors['primary'],
      '--background-color': theme.data.colors['background'],
      '--card-color': theme.data.colors['card'],
      '--stroke': theme.data.colors['stroke'],
      '--primary-text': theme.data.colors['primary_text'],
      '--background-text': theme.data.colors['background_text'],
      '--graph-0': theme.data.colors['graph_0'],
      '--graph-1': theme.data.colors['graph_1'],
      '--graph-2': theme.data.colors['graph_2'],
      '--graph-3': theme.data.colors['graph_3'],
      '--graph-4': theme.data.colors['graph_4'],
      '--graph-5': theme.data.colors['graph_5'],
      '--graph-6': theme.data.colors['graph_6'],
      '--graph-7': theme.data.colors['graph_7'],
      '--graph-8': theme.data.colors['graph_8'],
      '--graph-9': theme.data.colors['graph_9'],
    }
    Object.entries(cssVariables).forEach(([key, value]) => {
      element.style.setProperty(key, value)
    })
    // 
    if (theme.data.fonts.textFont.url && theme.data.fonts.textFont.name) {
      useFontLoader({ [theme.data.fonts.textFont.name]: theme.data.fonts.textFont.url })
    }

    // Apply fonts to preview container
    element.style.setProperty('font-family', `"${theme.data.fonts.textFont.name}"`)
    element.style.setProperty('--heading-font-family', `"${theme.data.fonts.textFont.name}"`)
    element.style.setProperty('--body-font-family', `"${theme.data.fonts.textFont.name}"`)


  }

  const handleDelete = async () => {
    if (isDeleting) return;
    setIsDeleting(true);
    const response = await DashboardApi.deletePresentation(id);

    if (response?.success) {
      trackEvent(MixpanelEvent.Dashboard_Presentation_Deleted, {
        pathname,
        presentation_id: id,
        slide_count: presentation?.slides?.length || 0,
      });
      notify.success("Presentation deleted", "The presentation was removed from your dashboard.");
      setShowDeleteDialog(false);
      if (onDeleted) {
        onDeleted(id);
      }
    } else {
      notify.error("Could not delete presentation", response?.message || "Something went wrong while deleting the presentation.");
    }
    setIsDeleting(false);
  };

  const handleAccessChange = async (nextVisibility: PresentationVisibility) => {
    if (isUpdatingAccess || nextVisibility === visibility) return;

    const previousVisibility = visibility;
    setVisibility(nextVisibility);
    setIsUpdatingAccess(true);

    try {
      const updated = await DashboardApi.updatePresentationAccess(id, nextVisibility);
      setVisibility(updated.visibility || nextVisibility);
      notify.success("Presentation access updated", `Visibility changed to ${nextVisibility}.`);
    } catch (error) {
      setVisibility(previousVisibility);
      notify.error(
        "Could not update access",
        error instanceof Error ? error.message : "Something went wrong while updating presentation access."
      );
    } finally {
      setIsUpdatingAccess(false);
    }
  };

  const firstSlide = presentation?.slides?.[0];
  return (
    <Card
      suppressHydrationWarning={true}
      onClick={handlePreview}
      className="bg-[#F8FBFB] font-syne shadow-none sm:shadow-none  presentation-card rounded-[12px] p-0 group hover:shadow-md transition-all duration-500 slide-theme cursor-pointer overflow-hidden flex flex-col"
    >
      <div
        id={`dashboard-presentation-card-${id}`}
        suppressHydrationWarning={true} className="flex flex-col flex-1 relative z-40">
        {/* <p className=" text-xs font-syne absolute top-2 flex gap-1 capitalize  items-center left-2 rounded-[100px]  px-2.5 py-1 bg-[#3A3A3AF5] text-white font-semibold  z-40 ">

          {presentation.type}
        </p> */}

        <img src="/card_bg.svg" alt="" className="absolute top-0 left-0 w-full h-full object-cover" />
        <div className="scale-[0.75] mt-4  border border-gray-300 rounded-lg overflow-hidden">

          <SlideScale slide={firstSlide} isClickable={false} />
        </div>

        <div className="w-full py-3 px-5 mt-auto z-40 relative bg-white  border-t border-[#EDEEEF]">
          <div className="flex items-start justify-between gap-4 w-full">
            <div className="flex flex-col items-start gap-1">
              <div className="text-sm text-[#191919] font-semibold  overflow-hidden line-clamp-1">
                <MarkdownRenderer content={title} className="text-sm mb-0  font-syne text-[#191919] font-semibold  overflow-hidden line-clamp-1" />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-[#808080] text-sm font-syne">
                  {new Date(presentation?.created_at).toLocaleDateString()}
                </p>
                {canEditAccess ? (
                  <Select
                    value={visibility}
                    onValueChange={(value) =>
                      void handleAccessChange(value as PresentationVisibility)
                    }
                    disabled={isUpdatingAccess}
                  >
                    <SelectTrigger
                      className="h-7 w-[106px] rounded-full border-[#E6E8EC] bg-white px-2.5 py-0 text-xs font-medium text-[#4D5158] shadow-none focus:ring-0"
                      onClick={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                      }}
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent
                      align="start"
                      className="z-[70] bg-white"
                      onClick={(event) => event.stopPropagation()}
                    >
                      {VISIBILITY_OPTIONS.map((option) => {
                        const Icon = option.icon;
                        return (
                          <SelectItem key={option.value} value={option.value}>
                            <span className="flex items-center gap-2">
                              <Icon className="h-4 w-4" />
                              {option.label}
                            </span>
                          </SelectItem>
                        );
                      })}
                    </SelectContent>
                  </Select>
                ) : (
                  <VisibilityBadge visibility={visibility} />
                )}
              </div>

            </div>
            <Popover>
              <PopoverTrigger className="w-6 h-6 hover:bg-gray-100 rounded-full flex items-center justify-center text-gray-500 hover:text-gray-700" onClick={(e) => e.stopPropagation()}>
                <EllipsisVertical className="w-6 h-6 text-gray-500" />
              </PopoverTrigger>
              <PopoverContent align="end" className="bg-white w-[200px]">
                <button
                  className="flex items-center justify-between w-full px-2 py-1 hover:bg-gray-100"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setShowDeleteDialog(true);
                  }}
                >
                  <p>Delete</p>
                  <Trash className="w- h-4 text-red-500" />
                </button>
              </PopoverContent>
            </Popover>
          </div>

        </div>
      </div>
      {showDeleteDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center animate-[fadeIn_150ms_ease-out]"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            if (isDeleting) return;
            setShowDeleteDialog(false);
          }}
        >
          <div className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" />
          <div
            className="relative w-[360px] rounded-2xl bg-white shadow-2xl animate-[scaleIn_200ms_ease-out]"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
          >
            <div className="flex flex-col items-center p-6 pb-4 text-center">
              <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-red-50">
                <AlertTriangle className="h-6 w-6 text-red-500" />
              </div>
              <h3 className="mb-2 text-lg font-semibold text-[#191919]">
                Delete Presentation?
              </h3>
              <p className="text-sm leading-relaxed text-gray-500">
                You are about to delete{" "}
                <span className="font-medium text-gray-700">&quot;{title}&quot;</span>.
                This action cannot be undone.
              </p>
            </div>
            <div className="flex border-t border-gray-100">
              <button
                onClick={() => setShowDeleteDialog(false)}
                disabled={isDeleting}
                className="flex-1 px-4 py-3.5 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={() => void handleDelete()}
                disabled={isDeleting}
                className="flex flex-1 items-center justify-center gap-2 border-l border-gray-100 px-4 py-3.5 text-sm font-medium text-red-500 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isDeleting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Deleting...
                  </>
                ) : (
                  "Delete"
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
};

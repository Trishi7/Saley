// The body of the person drawer below the Email field (R5 T14): the facts (Follow-up by,
// priority, links, papers, working on, next step, notes, deal), the touches as the timeline
// strip (the same strip as Accounts) and the stage history. No Evidence row (brief).
import { GlobeIcon, IdCardIcon } from "lucide-react";
import { ExternalButton } from "@/components/saley/Links";
import { TouchTimeline } from "@/components/saley/TouchTimeline";
import { W } from "@/words";
import { DrawerSection, FactList, History } from "./DrawerParts";
import { FollowUpLabel, FollowUpValue, Papers, WorkingOnValue } from "./PersonFacts";
import type { Opportunity, PersonDetail } from "./types";

function deal(o: Opportunity | null): string | null {
  if (!o) return null;
  const parts = [o.status, o.deal_status, o.prob !== null ? `${o.prob}%` : null, o.size_text];
  return parts.filter(Boolean).join(" · ") || null;
}

export function PersonDetails({ item }: { item: PersonDetail }) {
  const website = item.site_href ? (
    <ExternalButton href={item.site_href} label={W.openWebsite} icon={<GlobeIcon aria-hidden />}>
      {W.openWebsite}
    </ExternalButton>
  ) : (
    item.site_text
  );
  return (
    <>
      <FactList
        facts={[
          [
            <FollowUpLabel key="label" rule={item.follow_up_by?.rule} />,
            item.follow_up_by?.name ? (
              <FollowUpValue key="value" value={item.follow_up_by} />
            ) : null,
          ],
          [W.dPriority, item.priority],
          [W.dCity, item.city],
          [W.dIndustry, item.industry],
          [
            W.dLinkedIn,
            item.li_href ? (
              <ExternalButton
                href={item.li_href}
                label={W.openLinkedIn}
                icon={<IdCardIcon aria-hidden />}
              >
                {W.openLinkedIn}
              </ExternalButton>
            ) : null,
          ],
          [W.dWebsite, website],
          [W.dPapers, <Papers key="papers" papers={item.papers} label={item.papers_label} />],
          [
            W.dWorkingOn,
            item.working_on.found ? <WorkingOnValue key="working" value={item.working_on} /> : null,
          ],
          [W.dNextStep, item.next_step],
          [W.dLiStatus, item.li_status],
          [W.dNotes, item.notes],
          [W.dDeal, deal(item.opportunity)],
        ]}
      />
      <DrawerSection title={W.dTouches}>
        <TouchTimeline timeline={item.timeline} />
      </DrawerSection>
      <DrawerSection title={W.dHistory}>
        <History entries={item.stage_history} />
      </DrawerSection>
    </>
  );
}

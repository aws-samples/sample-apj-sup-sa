// ============================================================================
// AnyCompanyPay routing — OOH SCHEDULER (Pattern B: after-hours backlog)
// ============================================================================
// A case is not routable, so an after-hours contact used to leave only a case
// for agents to sort by hand. This Lambda turns every OOH merchant contact into
// a case PLUS a scheduled, tier-prioritised task that routes to the backlog
// agents at the next opening (design §5).
//
// action=intake      (routed inbound flow, closed branch)
//   1. tier from Customer Profiles
//   2. find the merchant's open case (the chat's own case_id first, else the
//      latest open case for the merchant) or create one from the OOH template
//   3. link the contact + add a comment
//   4. dedup: if the case already has a live pending OOH task, stop (one case
//      and at most one pending task per merchant, design G4)
//   5. next opening (GetEffectiveHoursOfOperations, overrides applied, capped
//      at the 6-day scheduling limit) -> StartTaskContact(ScheduledTime)
//   6. case: ooh_task_pending=true, ooh_task_id=<task>; link the task
//
// action=reschedule  (OOH task flow, when the task starts but hours are still
//   closed — closures longer than the 6-day limit): schedule the next hop,
//   linked to the current task, and point the case at it (design §5.2.1).
//
// Failures never drop the contact: the flow still acknowledges the merchant,
// and a scheduling failure is commented on the case and published to the
// supervisor alert topic (test B-07).
// ----------------------------------------------------------------------------
import {
  ConnectClient,
  DescribeContactCommand,
  GetEffectiveHoursOfOperationsCommand,
  ListContactFlowsCommand,
  StartTaskContactCommand,
} from "@aws-sdk/client-connect";
import {
  ConnectCasesClient,
  CreateCaseCommand,
  CreateRelatedItemCommand,
  GetCaseCommand,
  SearchCasesCommand,
  UpdateCaseCommand,
  type FieldValue,
  type FieldValueUnion,
} from "@aws-sdk/client-connectcases";
import { CustomerProfilesClient } from "@aws-sdk/client-customer-profiles";
import { PublishCommand, SNSClient } from "@aws-sdk/client-sns";
import {
  ConnectEvent,
  TIER_PRIORITY,
  Tier,
  caseFields,
  contactData,
  parseJsonMap,
  resolveTier,
} from "../shared/common";
import { computeNextOpening, isSchedulable, zonedDate, zonedIso } from "../shared/next-opening";

const REGION = process.env.AWS_REGION!;
const INSTANCE_ID = process.env.CONNECT_INSTANCE_ID!;
const CASES_DOMAIN_ID = process.env.CASES_DOMAIN_ID!;
const OOH_TEMPLATE_ID = process.env.OOH_TEMPLATE_ID!;
// The task flow invokes THIS Lambda (re-schedule), so its ARN can't also be an
// env var here (CloudFormation cycle): resolve it by name once per container.
const OOH_TASK_FLOW_NAME = process.env.OOH_TASK_FLOW_NAME!;
const CS_HOURS_ID = (process.env.CS_HOURS_ARN || "").split("/").pop() || "";
const PROFILES_DOMAIN = process.env.PROFILES_DOMAIN_NAME || "";
const TIER_DEFAULTS = parseJsonMap(process.env.TIER_DEFAULTS);
const OPEN_OFFSET_SECONDS = Number(process.env.OPEN_OFFSET_SECONDS || 0);
const DEMO_DELAY_SECONDS = Number(process.env.DEMO_DELAY_SECONDS || 0);
const ALERT_TOPIC_ARN = process.env.ALERT_TOPIC_ARN || "";
// GetEffectiveHoursOfOperations look-ahead (covers a closure beyond the 6-day cap).
const LOOKAHEAD_DAYS = 13;
const CLOSED = new Set(["closed", "resolved"]);
const CASE_PRIORITY: Record<Tier, string> = { VIP: "High", key: "Medium", shared: "Low" };

const connect = new ConnectClient({ region: REGION });
const cases = new ConnectCasesClient({ region: REGION });
const profiles = new CustomerProfilesClient({ region: REGION });
const sns = new SNSClient({ region: REGION });

const str = (v: string): FieldValueUnion => ({ stringValue: v });

let taskFlowId: string | null = null;
async function oohTaskFlowId(): Promise<string> {
  if (taskFlowId) return taskFlowId;
  let next: string | undefined;
  do {
    const r = await connect.send(
      new ListContactFlowsCommand({ InstanceId: INSTANCE_ID, ContactFlowTypes: ["CONTACT_FLOW"], NextToken: next })
    );
    const hit = (r.ContactFlowSummaryList ?? []).find((f) => f.Name === OOH_TASK_FLOW_NAME);
    if (hit?.Id) return (taskFlowId = hit.Id);
    next = r.NextToken;
  } while (next);
  throw new Error(`OOH task flow "${OOH_TASK_FLOW_NAME}" not found`);
}

async function alert(subject: string, message: string) {
  console.error(`ALERT ${subject}: ${message}`);
  if (!ALERT_TOPIC_ARN) return;
  try {
    await sns.send(new PublishCommand({ TopicArn: ALERT_TOPIC_ARN, Subject: subject.slice(0, 99), Message: message }));
  } catch (err) {
    console.error("alert publish failed", err);
  }
}

async function comment(caseId: string, body: string) {
  try {
    await cases.send(
      new CreateRelatedItemCommand({
        domainId: CASES_DOMAIN_ID,
        caseId,
        type: "Comment",
        content: { comment: { body: body.slice(0, 3000), contentType: "Text/Plain" } },
      })
    );
  } catch (err) {
    console.warn(`comment on case ${caseId} failed`, err);
  }
}

async function linkContact(caseId: string, instanceArn: string, contactId: string) {
  try {
    await cases.send(
      new CreateRelatedItemCommand({
        domainId: CASES_DOMAIN_ID,
        caseId,
        type: "Contact",
        content: { contact: { contactArn: `${instanceArn}/contact/${contactId}` } },
      })
    );
  } catch (err) {
    console.warn(`link contact ${contactId} -> case ${caseId} failed`, err);
  }
}

interface OpenCase {
  caseId: string;
  pendingTaskId: string; // ooh_task_id when ooh_task_pending=true
}

function pick(fields: FieldValue[] | undefined, id: string | undefined): string {
  if (!id) return "";
  const v = (fields ?? []).find((x) => x.id === id)?.value;
  return (v?.stringValue ?? "") as string;
}

/**
 * Which case an after-hours request goes on (the merchant has already said "yes,
 * log a case"):
 *   1. a chat started FROM a case -> that case (if open + same tenant);
 *   2. tonight's after-hours case: an open case of this merchant whose follow-up
 *      task is still pending (no duplicate follow-ups for repeat contacts);
 *   3. otherwise null -> a NEW case. Older, unrelated open cases are never reused.
 */
async function findOpenCase(merchantId: string, preferredCaseId: string): Promise<OpenCase | null> {
  const f = await caseFields(cases, CASES_DOMAIN_ID);
  const want = ["status", f["case_status"], f["merchant_id"], f["ooh_task_pending"], f["ooh_task_id"]].filter(
    Boolean
  ) as string[];
  const toOpen = (caseId: string, fields: FieldValue[] | undefined): OpenCase | null => {
    const statuses = [pick(fields, "status"), pick(fields, f["case_status"])].map((s) => s.toLowerCase());
    if (statuses.some((s) => CLOSED.has(s))) return null;
    if (pick(fields, f["merchant_id"]) !== merchantId) return null;
    const pending = pick(fields, f["ooh_task_pending"]) === "true";
    return { caseId, pendingTaskId: pending ? pick(fields, f["ooh_task_id"]) : "" };
  };

  if (preferredCaseId) {
    try {
      const res = await cases.send(
        new GetCaseCommand({ domainId: CASES_DOMAIN_ID, caseId: preferredCaseId, fields: want.map((id) => ({ id })) })
      );
      const c = toOpen(preferredCaseId, res.fields);
      if (c) return c;
    } catch (err) {
      console.warn(`GetCase ${preferredCaseId} failed; searching by merchant`, err);
    }
  }
  if (!merchantId || !f["merchant_id"]) return null;
  const res = await cases.send(
    new SearchCasesCommand({
      domainId: CASES_DOMAIN_ID,
      maxResults: 10,
      filter: {
        andAll: [
          { field: { equalTo: { id: f["merchant_id"], value: str(merchantId) } } },
          { field: { equalTo: { id: "status", value: str("open") } } },
          { field: { equalTo: { id: f["ooh_task_pending"], value: str("true") } } },
        ],
      },
      sorts: [{ fieldId: "last_updated_datetime", sortOrder: "Desc" }],
      fields: want.map((id) => ({ id })),
    })
  );
  for (const c of res.cases ?? []) {
    const oc = c?.caseId ? toOpen(c.caseId, c.fields) : null;
    if (oc?.pendingTaskId && (await taskStillLive(oc.pendingTaskId))) return oc;
  }
  return null;
}

async function createOohCase(opts: {
  merchantId: string;
  merchantName: string;
  tier: Tier;
  profileArn: string;
}): Promise<string> {
  const f = await caseFields(cases, CASES_DOMAIN_ID);
  const fields = [
    { id: "title", value: str(`After-hours request — ${opts.merchantName || opts.merchantId}`) },
    { id: f["summary"], value: str("Logged from live chat outside business hours, at the merchant's request. An agent follows up at the next opening.") },
    { id: f["priority"], value: str(CASE_PRIORITY[opts.tier]) },
    { id: f["case_status"], value: str("Open") },
    { id: f["merchant"], value: str(opts.merchantName) },
    { id: f["merchant_id"], value: str(opts.merchantId) },
    { id: f["tier"], value: str(opts.tier) },
    { id: f["ooh_task_pending"], value: str("false") },
  ].filter((x) => x.id);
  const create = (withCustomer: boolean) =>
    cases.send(
      new CreateCaseCommand({
        domainId: CASES_DOMAIN_ID,
        templateId: OOH_TEMPLATE_ID,
        fields: withCustomer && opts.profileArn ? [...fields, { id: "customer_id", value: str(opts.profileArn) }] : fields,
      })
    );
  try {
    return (await create(true)).caseId!;
  } catch (err) {
    // The customer link is nice-to-have (agent workspace profile panel); never
    // fail the OOH intake on it.
    if (!opts.profileArn) throw err;
    console.warn("CreateCase with customer_id failed; retrying without", err);
    return (await create(false)).caseId!;
  }
}

/** True while the task exists and has not ended (scheduled or in queue/with an agent). */
async function taskStillLive(taskId: string): Promise<boolean> {
  if (!taskId) return false;
  try {
    const res = await connect.send(new DescribeContactCommand({ InstanceId: INSTANCE_ID, ContactId: taskId }));
    return !res.Contact?.DisconnectTimestamp;
  } catch (err) {
    console.warn(`DescribeContact ${taskId} failed; treating as not live`, err);
    return false;
  }
}

async function nextOpening(nowMs: number) {
  // Overrides (holidays) are already applied by GetEffectiveHoursOfOperations;
  // dates are interpreted in the hours' own time zone.
  const probe = await connect.send(
    new GetEffectiveHoursOfOperationsCommand({
      InstanceId: INSTANCE_ID,
      HoursOfOperationId: CS_HOURS_ID,
      FromDate: new Date(nowMs - 24 * 3600 * 1000).toISOString().slice(0, 10),
      ToDate: new Date(nowMs + LOOKAHEAD_DAYS * 24 * 3600 * 1000).toISOString().slice(0, 10),
    })
  );
  const timeZone = probe.TimeZone || "UTC";
  const r = computeNextOpening({
    days: probe.EffectiveHoursOfOperationList ?? [],
    timeZone,
    nowMs,
    openOffsetSeconds: OPEN_OFFSET_SECONDS,
    demoDelaySeconds: DEMO_DELAY_SECONDS,
  });
  const human = (s: number) =>
    new Intl.DateTimeFormat("en-GB", {
      timeZone,
      weekday: "short",
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(new Date(s * 1000));
  return {
    ...r,
    timeZone,
    nowLocal: human(Math.floor(nowMs / 1000)),
    scheduleLocal: zonedIso(r.scheduleEpoch * 1000, timeZone),
    nextOpenLocal: r.nextOpenEpoch ? human(r.nextOpenEpoch) : `after ${zonedDate(r.scheduleEpoch * 1000, timeZone)}`,
  };
}

async function scheduleTask(opts: {
  caseId: string;
  relatedContactId: string;
  clientToken: string;
  merchantId: string;
  merchantName: string;
  tier: Tier;
  scheduleEpoch: number;
  instanceArn: string;
}): Promise<string> {
  const res = await connect.send(
    new StartTaskContactCommand({
      InstanceId: INSTANCE_ID,
      ContactFlowId: await oohTaskFlowId(),
      // RelatedContactId (not PreviousContactId): no 12-task chain limit (design §2.5).
      RelatedContactId: opts.relatedContactId,
      ClientToken: opts.clientToken.slice(0, 500),
      Name: `OOH follow-up — ${opts.merchantName || opts.merchantId}`.slice(0, 512),
      Description: `After-hours follow-up for case ${opts.caseId} (${opts.tier}).`,
      ScheduledTime: new Date(opts.scheduleEpoch * 1000),
      Attributes: {
        merchant_id: opts.merchantId,
        merchant_name: opts.merchantName,
        case_id: opts.caseId,
        tier: opts.tier,
        tierPriority: TIER_PRIORITY[opts.tier],
        afterHours: "true",
        scheduledTaskTime: String(opts.scheduleEpoch),
      },
    })
  );
  const taskId = res.ContactId!;
  await linkContact(opts.caseId, opts.instanceArn, taskId);
  const f = await caseFields(cases, CASES_DOMAIN_ID);
  await cases.send(
    new UpdateCaseCommand({
      domainId: CASES_DOMAIN_ID,
      caseId: opts.caseId,
      fields: [
        { id: f["ooh_task_pending"], value: str("true") },
        { id: f["ooh_task_id"], value: str(taskId) },
      ].filter((x) => x.id),
    })
  );
  return taskId;
}

async function intake(event: ConnectEvent) {
  const { contactId, instanceArn, attributes } = contactData(event);
  const merchantId = attributes.merchant_id ?? "";
  const merchantName = attributes.merchant_name ?? "";
  const now = Date.now();

  const { tier, profileArn } = await resolveTier(profiles, PROFILES_DOMAIN, merchantId, TIER_DEFAULTS);
  const found = await findOpenCase(merchantId, attributes.case_id ?? "");
  const caseId = found?.caseId ?? (await createOohCase({ merchantId, merchantName, tier, profileArn }));
  await linkContact(caseId, instanceArn, contactId);
  const when = await nextOpening(now);
  const base = { caseId, caseRef: caseId.slice(0, 8), tier, nextOpenLocal: when.nextOpenLocal, caseCreated: String(!found) };

  // Dedup: one pending OOH task per case/merchant.
  if (found?.pendingTaskId && (await taskStillLive(found.pendingTaskId))) {
    await comment(
      caseId,
      `Another chat received outside business hours (${when.nowLocal}). The follow-up already scheduled covers it.` +
        ` [chat ${contactId.slice(0, 8)}]`
    );
    return { ...base, result: "task-pending", taskId: found.pendingTaskId };
  }

  if (!isSchedulable(when.scheduleEpoch, now)) {
    const msg = `Computed schedule time ${when.scheduleEpoch} is not in the future; no task created for case ${caseId}.`;
    await comment(caseId, `OOH task NOT scheduled: ${msg}`);
    await alert("AnyCompanyPay OOH scheduling failed", msg);
    return { ...base, result: "task-failed", taskId: "" };
  }
  try {
    const taskId = await scheduleTask({
      caseId,
      relatedContactId: contactId,
      clientToken: `ooh-${contactId}`,
      merchantId,
      merchantName,
      tier,
      scheduleEpoch: when.scheduleEpoch,
      instanceArn,
    });
    await comment(
      caseId,
      `Chat received outside business hours (${when.nowLocal}). An agent will follow up when we open ` +
        `(${when.nextOpenLocal})${when.reschedule ? "; the follow-up re-schedules itself for closures over 6 days" : ""}.` +
        ` [chat ${contactId.slice(0, 8)}, follow-up task ${taskId.slice(0, 8)}]`
    );
    return { ...base, result: "task-created", taskId, reschedule: String(when.reschedule) };
  } catch (err) {
    const msg = `StartTaskContact failed for case ${caseId}: ${err instanceof Error ? err.message : String(err)}`;
    await comment(caseId, `OOH task NOT scheduled: ${msg}`);
    await alert("AnyCompanyPay OOH scheduling failed", msg);
    return { ...base, result: "task-failed", taskId: "" };
  }
}

async function reschedule(event: ConnectEvent) {
  const { contactId, instanceArn, attributes } = contactData(event);
  const caseId = attributes.case_id ?? "";
  const tier = (attributes.tier as Tier) || "shared";
  const now = Date.now();
  const when = await nextOpening(now);
  if (!caseId || !isSchedulable(when.scheduleEpoch, now)) {
    const msg = `Cannot re-schedule OOH task ${contactId} (case ${caseId || "unknown"}).`;
    await alert("AnyCompanyPay OOH re-schedule failed", msg);
    return { result: "reschedule-failed" };
  }
  const taskId = await scheduleTask({
    caseId,
    relatedContactId: contactId,
    clientToken: `ooh-resched-${contactId}`,
    merchantId: attributes.merchant_id ?? "",
    merchantName: attributes.merchant_name ?? "",
    tier,
    scheduleEpoch: when.scheduleEpoch,
    instanceArn,
  });
  await comment(caseId, `Still closed when task ${contactId} started; re-scheduled as ${taskId} for ${when.scheduleLocal}.`);
  return { result: "rescheduled", taskId, nextOpenLocal: when.nextOpenLocal };
}

export const handler = async (event: ConnectEvent) => {
  const action = event?.Details?.Parameters?.action ?? "intake";
  try {
    const out = action === "reschedule" ? await reschedule(event) : await intake(event);
    console.log("ooh-scheduler result", JSON.stringify({ action, ...out }));
    return out;
  } catch (err) {
    // Surface to the supervisor, then fail the invocation so the flow takes its
    // Error branch (generic acknowledgement; the contact is never dropped silently).
    const { contactId } = contactData(event);
    await alert(`AnyCompanyPay OOH ${action} error`, `contact ${contactId}: ${err instanceof Error ? err.stack : String(err)}`);
    throw err;
  }
};

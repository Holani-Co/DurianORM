<script setup>
import { computed, watch, onMounted, ref } from 'vue';
import {
  useMapGetter,
  useFunctionGetter,
  useStore,
} from 'dashboard/composables/store';
import { useAccount } from 'dashboard/composables/useAccount';
import { useUISettings } from 'dashboard/composables/useUISettings';
import { FEATURE_FLAGS } from 'dashboard/featureFlags';

import AccordionItem from 'dashboard/components/Accordion/AccordionItem.vue';
import ContactConversations from './ContactConversations.vue';
import ConversationAction from './ConversationAction.vue';
import ConversationParticipant from './ConversationParticipant.vue';
import ContactInfo from './contact/ContactInfo.vue';
import ContactNotes from './contact/ContactNotes.vue';
import ConversationInfo from './ConversationInfo.vue';
import CustomAttributes from './customAttributes/CustomAttributes.vue';
import SharedFiles from './SharedFiles.vue';
import Draggable from 'vuedraggable';
import MacrosList from './Macros/List.vue';
import ShopifyOrdersList from 'dashboard/components/widgets/conversation/ShopifyOrdersList.vue';
import SidebarActionsHeader from 'dashboard/components-next/SidebarActionsHeader.vue';
import LinearIssuesList from 'dashboard/components/widgets/conversation/linear/IssuesList.vue';
import LinearSetupCTA from 'dashboard/components/widgets/conversation/linear/LinearSetupCTA.vue';
import ZohoTicketPanel from './ZohoTicketPanel.vue';
import ManualTicketCreate from './ManualTicketCreate.vue';
import ZohoTicketsListPanel from './ZohoTicketsListPanel.vue';
import ZohoCrmPanel from './ZohoCrmPanel.vue';
import ReviewEscalationPanel from './ReviewEscalationPanel.vue';
import RelatedTicketsPanel from './RelatedTicketsPanel.vue';
import PendingTicketDecisionPanel from './PendingTicketDecisionPanel.vue';
import PendingCategoryDecisionPanel from './PendingCategoryDecisionPanel.vue';

const props = defineProps({
  conversationId: {
    type: [Number, String],
    required: true,
  },
  inboxId: {
    type: Number,
    default: undefined,
  },
});

const {
  updateUISettings,
  isContactSidebarItemOpen,
  conversationSidebarItemsOrder,
  toggleSidebarUIState,
} = useUISettings();

const dragging = ref(false);
const conversationSidebarItems = ref([]);

const shopifyIntegration = useFunctionGetter(
  'integrations/getIntegration',
  'shopify'
);

const isShopifyFeatureEnabled = computed(
  () => shopifyIntegration.value.enabled
);

const { isCloudFeatureEnabled } = useAccount();

const isLinearFeatureEnabled = computed(() =>
  isCloudFeatureEnabled(FEATURE_FLAGS.LINEAR)
);

const linearIntegration = useFunctionGetter(
  'integrations/getIntegration',
  'linear'
);

const isLinearClientIdConfigured = computed(() => {
  return !!linearIntegration.value?.id;
});

const isLinearConnected = computed(
  () => linearIntegration.value?.enabled || false
);

const store = useStore();
const currentChat = useMapGetter('getSelectedChat');
const conversationId = computed(() => props.conversationId);
const conversationMetadataGetter = useMapGetter(
  'conversationMetadata/getConversationMetadata'
);
const currentConversationMetaData = computed(() =>
  conversationMetadataGetter.value(conversationId.value)
);
const conversationAdditionalAttributes = computed(
  () => currentConversationMetaData.value.additional_attributes || {}
);
// custom_attributes is the writable, user-facing JSON store on a conversation
// (additional_attributes is system-only: browser, referer, etc.). The zoho
// bridge writes the Zoho ticket card here so the sidebar panel can render it.
const conversationCustomAttributes = computed(
  () => currentChat.value?.custom_attributes || {}
);

// Google-review data lives on the conversation's additional_attributes (the
// bridge's reviews poller writes type / stars / reviewer / review_comment).
// The "Escalate to team" panel shows for 1★ reviews AND any review the AI
// flagged as genuinely bad (custom_attributes.review_negative — set by the
// poller when it hands the review off for a complaint/criticism/low rating).
const reviewAttributes = computed(
  () => currentChat.value?.additional_attributes || {}
);
const isBadReview = computed(
  () =>
    reviewAttributes.value.type === 'google_review' &&
    (Number(reviewAttributes.value.stars) === 1 ||
      conversationCustomAttributes.value.review_negative === true)
);

const channelType = computed(() => currentChat.value.meta?.channel);
// Manual ticket creation is an email-desk feature — show it only on email
// conversations that don't already have a Zoho ticket.
const isEmailChannel = computed(() => channelType.value === 'Channel::Email');
const hasZohoTicket = computed(
  () =>
    (conversationCustomAttributes.value.zoho_tickets || []).length > 0 ||
    Boolean(conversationCustomAttributes.value.zoho_ticket)
);

const contactGetter = useMapGetter('contacts/getContact');
const contactId = computed(() => currentChat.value.meta?.sender?.id);
const contact = computed(() => contactGetter.value(contactId.value));
const contactAdditionalAttributes = computed(
  () => contact.value.additional_attributes || {}
);

const getContactDetails = () => {
  if (contactId.value) {
    store.dispatch('contacts/show', { id: contactId.value });
  }
};

watch(contactId, (newContactId, prevContactId) => {
  if (newContactId && newContactId !== prevContactId) {
    getContactDetails();
  }
});

const onDragEnd = () => {
  dragging.value = false;
  updateUISettings({
    conversation_sidebar_items_order: conversationSidebarItems.value,
  });
};

const closeContactPanel = () => {
  updateUISettings({
    is_contact_sidebar_open: false,
    is_copilot_panel_open: false,
  });
};

onMounted(() => {
  conversationSidebarItems.value = conversationSidebarItemsOrder.value;
  getContactDetails();
  store.dispatch('attributes/get', 0);
  // Load integrations to ensure linear integration state is available
  store.dispatch('integrations/get', 'linear');
});
</script>

<template>
  <div class="w-full">
    <SidebarActionsHeader
      :title="$t('CONVERSATION.SIDEBAR.CONTACT')"
      @close="closeContactPanel"
    />
    <ContactInfo :contact="contact" :channel-type="channelType" />
    <!-- Zoho Desk ticket panel: populated by the zoho-bridge sidecar when it
         creates a ticket (manual bot handoff, priority escalation, AI
         signal escalation, auto-routed Legal). The bridge writes the
         full history into `custom_attributes.zoho_tickets` (an array,
         newest first) — render the entire array so agents see every
         escalation, not just the latest.
         The legacy `zoho_ticket` singular key is kept as a read-side
         fallback for any conversation the backfill script missed —
         without it, those would show "no ticket" in the sidebar even
         though they have one in Zoho. Routed through the single-ticket
         component since the legacy key was always a single ticket. -->
    <div id="sidebar-section-zoho-tickets" class="px-2 pt-3">
      <AccordionItem
        title="Zoho Desk"
        :is-open="isContactSidebarItemOpen('is_zoho_ticket_open')"
        compact
        @toggle="value => toggleSidebarUIState('is_zoho_ticket_open', value)"
      >
        <ZohoTicketsListPanel
          v-if="(conversationCustomAttributes.zoho_tickets || []).length"
          :tickets="conversationCustomAttributes.zoho_tickets"
        />
        <ZohoTicketPanel
          v-else
          :ticket="conversationCustomAttributes.zoho_ticket"
        />
        <ManualTicketCreate
          v-if="isEmailChannel && !hasZohoTicket && currentChat && currentChat.id"
          :conversation-id="currentChat.id"
        />
      </AccordionItem>
    </div>
    <!-- Zoho CRM panel — Contact / Lead / Deal ids + Create buttons.
         Always rendered; the panel itself decides which rows and buttons
         to show based on custom_attributes + email category. -->
    <div id="sidebar-section-zoho-crm" class="px-2 pt-3">
      <AccordionItem
        title="Zoho CRM"
        :is-open="isContactSidebarItemOpen('is_zoho_crm_open')"
        compact
        @toggle="value => toggleSidebarUIState('is_zoho_crm_open', value)"
      >
        <ZohoCrmPanel
          v-if="currentChat && currentChat.id"
          :conversation-id="currentChat.id"
          :custom-attributes="conversationCustomAttributes"
        />
      </AccordionItem>
    </div>
    <!-- Escalate to team — only for bad Google reviews (1–2★). Emails the
         review (or full history) to addresses the agent enters. -->
    <div
      v-if="isBadReview"
      id="sidebar-section-review-escalation"
      class="px-2 pt-3"
    >
      <AccordionItem
        title="Escalate review"
        :is-open="isContactSidebarItemOpen('is_review_escalation_open')"
        compact
        @toggle="
          value => toggleSidebarUIState('is_review_escalation_open', value)
        "
      >
        <ReviewEscalationPanel
          v-if="currentChat && currentChat.id"
          :conversation-id="currentChat.id"
          :review="reviewAttributes"
        />
      </AccordionItem>
    </div>
    <!-- Pending ticket decision: written by zoho-bridge when it would have
         auto-created a Zoho ticket but the contact already has open ones.
         Renders only while `pending_zoho_ticket` is set; the bridge clears
         the attribute after the agent picks Attach or Create-new. -->
    <div
      v-if="conversationCustomAttributes.pending_zoho_ticket"
      id="sidebar-section-ticket-decision"
      class="px-2 pt-3"
    >
      <AccordionItem
        title="Ticket decision needed"
        :is-open="isContactSidebarItemOpen('is_ticket_decision_open')"
        compact
        @toggle="
          value => toggleSidebarUIState('is_ticket_decision_open', value)
        "
      >
        <PendingTicketDecisionPanel
          :pending="conversationCustomAttributes.pending_zoho_ticket"
          :conversation-id="conversationId"
        />
      </AccordionItem>
    </div>
    <!-- Pending category decision: written by zoho-bridge when it classified
         an email with LOW confidence and did NOT auto-forward. The agent
         confirms the category; the bridge then forwards + routes. -->
    <div
      v-if="conversationCustomAttributes.pending_category_decision"
      id="sidebar-section-category-decision"
      class="px-2 pt-3"
    >
      <AccordionItem
        title="Category decision needed"
        :is-open="isContactSidebarItemOpen('is_category_decision_open')"
        compact
        @toggle="
          value => toggleSidebarUIState('is_category_decision_open', value)
        "
      >
        <PendingCategoryDecisionPanel
          :pending="conversationCustomAttributes.pending_category_decision"
          :conversation-id="conversationId"
        />
      </AccordionItem>
    </div>
    <!-- Related Tickets panel: hints from Zoho's search of past tickets that
         match this conversation's subject. Helps agents spot duplicate /
         already-reported issues at a glance. -->
    <div
      v-if="(conversationCustomAttributes.related_tickets || []).length"
      class="px-2 pt-3"
    >
      <AccordionItem
        title="Related Tickets"
        :is-open="isContactSidebarItemOpen('is_related_tickets_open')"
        compact
        @toggle="
          value => toggleSidebarUIState('is_related_tickets_open', value)
        "
      >
        <RelatedTicketsPanel
          :tickets="conversationCustomAttributes.related_tickets"
        />
      </AccordionItem>
    </div>
    <div class="px-2 pt-3 pb-8 list-group">
      <Draggable
        :list="conversationSidebarItems"
        animation="200"
        ghost-class="ghost"
        handle=".drag-handle"
        item-key="name"
        class="flex flex-col gap-3"
        @start="dragging = true"
        @end="onDragEnd"
      >
        <template #item="{ element }">
          <div
            v-if="element.name === 'conversation_actions'"
            class="conversation--actions"
          >
            <AccordionItem
              :title="$t('CONVERSATION_SIDEBAR.ACCORDION.CONVERSATION_ACTIONS')"
              :is-open="isContactSidebarItemOpen('is_conv_actions_open')"
              @toggle="
                value => toggleSidebarUIState('is_conv_actions_open', value)
              "
            >
              <ConversationAction
                :conversation-id="conversationId"
                :inbox-id="inboxId"
              />
            </AccordionItem>
          </div>
          <div
            v-else-if="element.name === 'conversation_participants'"
            class="conversation--actions"
          >
            <AccordionItem
              :title="$t('CONVERSATION_PARTICIPANTS.SIDEBAR_TITLE')"
              :is-open="isContactSidebarItemOpen('is_conv_participants_open')"
              @toggle="
                value =>
                  toggleSidebarUIState('is_conv_participants_open', value)
              "
            >
              <ConversationParticipant
                :conversation-id="conversationId"
                :inbox-id="inboxId"
              />
            </AccordionItem>
          </div>
          <div v-else-if="element.name === 'conversation_info'">
            <AccordionItem
              :title="$t('CONVERSATION_SIDEBAR.ACCORDION.CONVERSATION_INFO')"
              :is-open="isContactSidebarItemOpen('is_conv_details_open')"
              compact
              @toggle="
                value => toggleSidebarUIState('is_conv_details_open', value)
              "
            >
              <ConversationInfo
                :conversation-attributes="conversationAdditionalAttributes"
                :contact-attributes="contactAdditionalAttributes"
              />
            </AccordionItem>
          </div>
          <div v-else-if="element.name === 'contact_attributes'">
            <AccordionItem
              :title="$t('CONVERSATION_SIDEBAR.ACCORDION.CONTACT_ATTRIBUTES')"
              :is-open="isContactSidebarItemOpen('is_contact_attributes_open')"
              compact
              @toggle="
                value =>
                  toggleSidebarUIState('is_contact_attributes_open', value)
              "
            >
              <CustomAttributes
                attribute-type="contact_attribute"
                attribute-from="conversation_contact_panel"
                :contact-id="contact.id"
                :empty-state-message="
                  $t('CONVERSATION_CUSTOM_ATTRIBUTES.NO_RECORDS_FOUND')
                "
              />
            </AccordionItem>
          </div>
          <div v-else-if="element.name === 'previous_conversation'">
            <AccordionItem
              v-if="contact.id"
              :title="
                $t('CONVERSATION_SIDEBAR.ACCORDION.PREVIOUS_CONVERSATION')
              "
              :is-open="isContactSidebarItemOpen('is_previous_conv_open')"
              compact
              @toggle="
                value => toggleSidebarUIState('is_previous_conv_open', value)
              "
            >
              <ContactConversations
                :contact-id="contact.id"
                :conversation-id="conversationId"
              />
            </AccordionItem>
          </div>
          <woot-feature-toggle
            v-else-if="element.name === 'macros'"
            feature-key="macros"
          >
            <AccordionItem
              :title="$t('CONVERSATION_SIDEBAR.ACCORDION.MACROS')"
              :is-open="isContactSidebarItemOpen('is_macro_open')"
              compact
              @toggle="value => toggleSidebarUIState('is_macro_open', value)"
            >
              <MacrosList :conversation-id="conversationId" />
            </AccordionItem>
          </woot-feature-toggle>
          <div
            v-else-if="
              element.name === 'linear_issues' &&
              isLinearFeatureEnabled &&
              isLinearClientIdConfigured
            "
          >
            <AccordionItem
              :title="$t('CONVERSATION_SIDEBAR.ACCORDION.LINEAR_ISSUES')"
              :is-open="isContactSidebarItemOpen('is_linear_issues_open')"
              compact
              @toggle="
                value => toggleSidebarUIState('is_linear_issues_open', value)
              "
            >
              <LinearSetupCTA v-if="!isLinearConnected" />
              <LinearIssuesList v-else :conversation-id="conversationId" />
            </AccordionItem>
          </div>
          <div
            v-else-if="
              element.name === 'shopify_orders' && isShopifyFeatureEnabled
            "
          >
            <AccordionItem
              :title="$t('CONVERSATION_SIDEBAR.ACCORDION.SHOPIFY_ORDERS')"
              :is-open="isContactSidebarItemOpen('is_shopify_orders_open')"
              compact
              @toggle="
                value => toggleSidebarUIState('is_shopify_orders_open', value)
              "
            >
              <ShopifyOrdersList :contact-id="contactId" />
            </AccordionItem>
          </div>
          <div v-else-if="element.name === 'contact_notes'">
            <AccordionItem
              :title="$t('CONVERSATION_SIDEBAR.ACCORDION.CONTACT_NOTES')"
              :is-open="isContactSidebarItemOpen('is_contact_notes_open')"
              compact
              @toggle="
                value => toggleSidebarUIState('is_contact_notes_open', value)
              "
            >
              <ContactNotes :contact-id="contactId" />
            </AccordionItem>
          </div>
          <div v-else-if="element.name === 'shared_files'">
            <AccordionItem
              :title="$t('CONVERSATION_SIDEBAR.ACCORDION.SHARED_FILES')"
              :is-open="isContactSidebarItemOpen('is_shared_files_open')"
              compact
              @toggle="
                value => toggleSidebarUIState('is_shared_files_open', value)
              "
            >
              <SharedFiles />
            </AccordionItem>
          </div>
        </template>
      </Draggable>
    </div>
  </div>
</template>

<style lang="scss" scoped>
::v-deep {
  .contact--profile {
    @apply pb-3 border-b border-solid border-n-weak;
  }
}
</style>

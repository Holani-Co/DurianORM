import { computed } from 'vue';
import { useStore } from 'vuex';
import { useMapGetter } from 'dashboard/composables/store';
import { INBOX_TYPES } from 'dashboard/helper/inbox';

/**
 * Resolve a deep-link URL that opens the current conversation in the
 * underlying communication platform (Meta Business Suite for FB/IG,
 * default mail/sms app for email/sms, wa.me for WhatsApp, etc.).
 *
 * Returns a reactive object {url, label, icon}. `url` is null when
 * we don't have a sensible deep-link for the conversation's channel
 * — in that case the button should hide itself.
 */
export const usePlatformLink = () => {
  const store = useStore();
  const currentChat = useMapGetter('getSelectedChat');
  const inboxesGetter = useMapGetter('inboxes/getInbox');

  const inbox = computed(() => {
    const id = currentChat.value?.inbox_id;
    return id ? inboxesGetter.value(id) : null;
  });

  const contact = computed(() => {
    const senderId = currentChat.value?.meta?.sender?.id;
    return senderId ? store.getters['contacts/getContact'](senderId) : null;
  });

  // Per-inbox identifier for the contact. For FB/IG that's the PSID (Page-Scoped ID).
  const sourceId = computed(() => {
    const inboxId = currentChat.value?.inbox_id;
    const ci = (contact.value?.contact_inboxes || []).find(
      c => c.inbox?.id === inboxId
    );
    return ci?.source_id || null;
  });

  // Convo "type" tag set by Meta webhook handlers — see HANDOFF.md.
  // Possible values: 'instagram_direct_message', 'instagram_comment',
  // 'facebook_comment'. Absent => Facebook DM.
  const convType = computed(
    () => currentChat.value?.additional_attributes?.type || null
  );

  // Phone numbers used in wa.me and sms: links MUST include a country code
  // (E.164-style, without the leading +). Chatwoot stores `contact.phone_number`
  // as the agent typed it on contact creation — there's no enforcement of
  // country-code presence, so a locally-formatted number like "(555) 123-4567"
  // here would yield a malformed wa.me/5551234567 link. Upstream callers
  // should validate phone shape; this composable just normalises whitespace
  // and punctuation. Returns digits only.
  const stripPhone = phone => (phone || '').replace(/[^\d]/g, '');

  const platform = computed(() => {
    const ch = inbox.value?.channel_type;
    if (!ch || !currentChat.value) return { url: null };

    // --- Facebook Page channel (DMs + Instagram-via-Facebook-Page + Comments) ---
    if (ch === INBOX_TYPES.FB) {
      const pageId = inbox.value?.page_id;
      const igBizId = inbox.value?.instagram_id;

      // Instagram DM landed on a Facebook Page channel (Kisnemanga setup).
      if (convType.value === 'instagram_direct_message') {
        if (igBizId) {
          return {
            url: `https://business.facebook.com/latest/inbox/instagram/?asset_id=${igBizId}`,
            label: 'Open in Instagram (Business Suite)',
            icon: 'i-ri-instagram-fill',
          };
        }
        return {
          url: `https://www.instagram.com/direct/inbox/`,
          label: 'Open in Instagram',
          icon: 'i-ri-instagram-fill',
        };
      }

      // Instagram comment — link to the post if we have it.
      if (convType.value === 'instagram_comment') {
        const link =
          currentChat.value?.additional_attributes?.permalink ||
          currentChat.value?.additional_attributes?.url ||
          `https://www.instagram.com/`;
        return {
          url: link,
          label: 'Open Instagram post',
          icon: 'i-ri-instagram-fill',
        };
      }

      // Facebook comment — link to the post if we have it.
      if (convType.value === 'facebook_comment') {
        const link =
          currentChat.value?.additional_attributes?.permalink_url ||
          currentChat.value?.additional_attributes?.url ||
          (pageId ? `https://www.facebook.com/${pageId}` : null);
        return {
          url: link,
          label: 'Open Facebook post',
          icon: 'i-ri-messenger-fill',
        };
      }

      // Plain Facebook page DM.
      if (pageId) {
        return {
          url: `https://business.facebook.com/latest/inbox/all_messages?asset_id=${pageId}`,
          label: 'Open in Messenger (Business Suite)',
          icon: 'i-ri-messenger-fill',
        };
      }
      if (sourceId.value) {
        return {
          url: `https://www.facebook.com/messages/t/${sourceId.value}`,
          label: 'Open in Messenger',
          icon: 'i-ri-messenger-fill',
        };
      }
    }

    // --- Native Instagram channel (Instagram Login API) ---
    // This channel is NOT backed by a Facebook Page, so Business Suite links
    // don't apply (asset_id expects a Page id) and ig.me/m/<handle> only works
    // for business handles, not customers' personal accounts.
    //
    // BEST link: instagram.com/direct/t/<thread_id> — opens the exact DM
    // thread in the brand's web inbox. The thread id is resolved at message
    // ingest (Messages::Instagram::MessageBuilder#ensure_dm_thread_id: the
    // Graph API conversation id base64-decodes to "ig_dm:<thread_id>") and
    // stored on conversation.additional_attributes.ig_thread_id. Note the
    // agent must be logged into the BRAND's Instagram account in the browser,
    // otherwise Instagram shows their own inbox instead.
    //
    // FALLBACK (thread id not yet resolved — e.g. old conversation that
    // hasn't received a message since the feature shipped): the customer's
    // PROFILE. Its "Message" button opens the existing thread without
    // creating a duplicate, so it's a reliable one-extra-click path. Falls
    // back further to the DM inbox when we don't even have a handle.
    if (ch === INBOX_TYPES.INSTAGRAM) {
      const convAttrs = currentChat.value?.additional_attributes || {};

      // Comment conversation → open the POST the comment is on. The permalink
      // is resolved + stored at ingest (Instagram::CommentService) because the
      // numeric media_id alone isn't usable in a URL.
      if (String(convAttrs.type || '').includes('comment') && convAttrs.permalink) {
        return {
          url: convAttrs.permalink,
          label: 'Open post on Instagram',
          icon: 'i-ri-instagram-fill',
        };
      }

      // DM with a resolved thread id → the exact thread in the web inbox.
      if (convAttrs.ig_thread_id) {
        return {
          url: `https://www.instagram.com/direct/t/${encodeURIComponent(convAttrs.ig_thread_id)}/`,
          label: 'Open conversation on Instagram',
          icon: 'i-ri-instagram-fill',
        };
      }

      // DM without a thread id (or a comment whose permalink wasn't
      // resolved) → the contact's profile; its "Message" button opens the
      // existing thread.
      const attrs = contact.value?.additional_attributes || {};
      const username =
        attrs.social_instagram_user_name ||
        attrs.social_profiles?.instagram ||
        attrs.username ||
        null;
      if (username) {
        return {
          url: `https://www.instagram.com/${encodeURIComponent(username)}/`,
          label: `Open @${username} on Instagram`,
          icon: 'i-ri-instagram-fill',
        };
      }
      return {
        url: 'https://www.instagram.com/direct/inbox/',
        label: 'Open in Instagram',
        icon: 'i-ri-instagram-fill',
      };
    }

    // --- WhatsApp (cloud / 360dialog / twilio) ---
    if (ch === INBOX_TYPES.WHATSAPP || (ch === INBOX_TYPES.TWILIO && inbox.value?.medium === 'whatsapp')) {
      const phone = stripPhone(contact.value?.phone_number);
      if (phone) {
        return {
          url: `https://wa.me/${phone}`,
          label: 'Open in WhatsApp',
          icon: 'i-ri-whatsapp-fill',
        };
      }
    }

    // --- Email channel ---
    // Open the inbox in its hosted webmail UI. We try to deep-link to the
    // EXACT thread using the customer email's Message-ID (rfc822msgid: in
    // Gmail's query syntax). Falls back to a contact-email search if no
    // Message-ID is available on any message in the conversation.
    if (ch === INBOX_TYPES.EMAIL) {
      const contactEmail = contact.value?.email;
      const inboxEmail = inbox.value?.email || '';
      const provider = (inbox.value?.provider || '').toLowerCase();
      const imapAddr = (inbox.value?.imap_address || '').toLowerCase();

      // Provider detection
      const isGoogle =
        provider === 'google' ||
        provider === 'gmail' ||
        imapAddr.includes('gmail') ||
        imapAddr.includes('google') ||
        inboxEmail.toLowerCase().endsWith('@gmail.com');

      const isMicrosoft =
        provider === 'microsoft' ||
        provider === 'outlook' ||
        imapAddr.includes('outlook') ||
        imapAddr.includes('office365') ||
        /@(outlook|hotmail|live|office365)\.com$/i.test(inboxEmail);

      // Hunt for the earliest INCOMING email's Message-ID on this conversation.
      // Chatwoot stores it on the message as either
      //   content_attributes.email.message_id  (preferred)
      //   source_id                            (older path / IMAP channel)
      // Outgoing/agent messages won't have it until they're actually sent,
      // so we filter for incoming first then fall back to any message.
      const messages = currentChat.value?.messages || [];
      const pickMessageId = msg => {
        const fromAttrs = msg?.content_attributes?.email?.message_id;
        if (fromAttrs) return fromAttrs;
        // source_id on email messages = the Message-ID header
        if (msg?.source_id && String(msg.source_id).includes('@')) {
          return msg.source_id;
        }
        return null;
      };
      const incomingFirst = [...messages].sort(
        (a, b) =>
          // incoming (0) before outgoing (1); then by created_at ascending
          (a.message_type ?? 0) - (b.message_type ?? 0) ||
          (a.created_at ?? 0) - (b.created_at ?? 0)
      );
      let messageId = null;
      for (const m of incomingFirst) {
        const id = pickMessageId(m);
        if (id) {
          messageId = id;
          break;
        }
      }

      if (contactEmail || inboxEmail || messageId) {
        if (isGoogle) {
          // `?authuser=<inbox-email>` opens the SPECIFIC Google account that
          // owns this Chatwoot inbox, not whichever /u/0/ defaults to.
          const authParam = inboxEmail
            ? `?authuser=${encodeURIComponent(inboxEmail)}`
            : '';

          if (messageId) {
            // rfc822msgid:<id> — when there's a unique match Gmail navigates
            // directly to the thread (no intermediate search list). Strip
            // any surrounding angle brackets that some headers carry.
            const cleaned = String(messageId).replace(/^<|>$/g, '');
            const q = encodeURIComponent(`rfc822msgid:${cleaned}`);
            return {
              url: `https://mail.google.com/mail/u/0/${authParam}#search/${q}`,
              label: inboxEmail
                ? `Open thread in Gmail (${inboxEmail})`
                : 'Open thread in Gmail',
              icon: 'i-ri-mail-fill',
            };
          }

          // Fallback: contact-email search (lists all messages with them)
          const q = contactEmail
            ? encodeURIComponent(`from:${contactEmail} OR to:${contactEmail}`)
            : 'in:inbox';
          return {
            url: `https://mail.google.com/mail/u/0/${authParam}#search/${q}`,
            label: inboxEmail
              ? `Open in Gmail (${inboxEmail})`
              : 'Open in Gmail',
            icon: 'i-ri-mail-fill',
          };
        }
        if (isMicrosoft) {
          // Outlook doesn't expose an analogous rfc822msgid: operator over
          // the web URL, so we keep search-by-email here for now.
          return {
            url: contactEmail
              ? `https://outlook.live.com/mail/0/inbox?search=${encodeURIComponent(contactEmail)}`
              : `https://outlook.live.com/mail/0/inbox`,
            label: 'Open in Outlook',
            icon: 'i-ri-mail-fill',
          };
        }
        // Unknown provider — fall back to a mailto: for a quick compose.
        if (contactEmail) {
          return {
            url: `mailto:${contactEmail}`,
            label: 'Compose email',
            icon: 'i-ri-mail-fill',
          };
        }
      }
    }

    // --- SMS (native or twilio sms) ---
    if (ch === INBOX_TYPES.SMS || (ch === INBOX_TYPES.TWILIO && inbox.value?.medium === 'sms')) {
      const phone = stripPhone(contact.value?.phone_number);
      if (phone) {
        return {
          url: `sms:+${phone}`,
          label: 'Open in SMS',
          icon: 'i-ri-message-3-fill',
        };
      }
    }

    // --- Telegram ---
    if (ch === INBOX_TYPES.TELEGRAM) {
      const handle =
        contact.value?.additional_attributes?.social_profiles?.telegram ||
        contact.value?.identifier;
      if (handle) {
        return {
          url: `https://t.me/${handle}`,
          label: 'Open in Telegram',
          icon: 'i-ri-telegram-fill',
        };
      }
    }

    // --- Twitter / X ---
    if (ch === INBOX_TYPES.TWITTER) {
      const twitterId = sourceId.value || contact.value?.identifier;
      if (twitterId) {
        return {
          url: `https://twitter.com/messages/compose?recipient_id=${twitterId}`,
          label: 'Open in X DMs',
          icon: 'i-ri-twitter-x-fill',
        };
      }
    }

    // --- Line ---
    if (ch === INBOX_TYPES.LINE) {
      const lineId = inbox.value?.line_channel_id;
      if (lineId) {
        return {
          url: `https://manager.line.biz/account/${lineId}/chat`,
          label: 'Open in LINE Official Account Manager',
          icon: 'i-ri-line-fill',
        };
      }
    }

    // Website / API / unknown — no platform to open.
    return { url: null };
  });

  return { platform };
};

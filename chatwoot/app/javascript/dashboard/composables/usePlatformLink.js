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

    // --- Native Instagram channel ---
    if (ch === INBOX_TYPES.INSTAGRAM) {
      const igId = inbox.value?.instagram_id || inbox.value?.user_id;
      if (igId) {
        return {
          url: `https://business.facebook.com/latest/inbox/instagram/?asset_id=${igId}`,
          label: 'Open in Instagram (Business Suite)',
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
    // Open the inbox in its hosted webmail UI rather than a local mailto:.
    // We can't link to the exact thread without the Message-ID, but a
    // search filter for the contact's address lands you next to it.
    if (ch === INBOX_TYPES.EMAIL) {
      const contactEmail = contact.value?.email;
      const inboxEmail = inbox.value?.email || '';
      const provider = (inbox.value?.provider || '').toLowerCase();
      const imapAddr = (inbox.value?.imap_address || '').toLowerCase();

      // Detect provider: explicit Channel::Email provider or IMAP host or email domain.
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

      if (contactEmail || inboxEmail) {
        if (isGoogle) {
          const q = contactEmail
            ? encodeURIComponent(`from:${contactEmail} OR to:${contactEmail}`)
            : 'in:inbox';
          // Use ?authuser=<inbox email> so Gmail opens the SPECIFIC account
          // that owns this Chatwoot inbox, not whichever Google account the
          // user happens to be signed into first (which /u/0/ defaults to).
          const authParam = inboxEmail
            ? `?authuser=${encodeURIComponent(inboxEmail)}`
            : '';
          return {
            url: `https://mail.google.com/mail/u/0/${authParam}#search/${q}`,
            label: inboxEmail
              ? `Open in Gmail (${inboxEmail})`
              : 'Open in Gmail',
            icon: 'i-ri-mail-fill',
          };
        }
        if (isMicrosoft) {
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

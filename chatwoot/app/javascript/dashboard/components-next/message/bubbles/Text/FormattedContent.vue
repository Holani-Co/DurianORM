<script setup>
import { computed } from 'vue';
import { useMessageContext } from '../../provider.js';

import MessageFormatter from 'shared/helpers/MessageFormatter.js';
import { MESSAGE_VARIANTS } from '../../constants';
import { emitter } from 'shared/helpers/mitt';
import { BUS_EVENTS } from 'shared/constants/busEvents';

const props = defineProps({
  content: {
    type: String,
    required: true,
  },
});

const { variant } = useMessageContext();

// `#cw-panel/<section>` links are sidebar shortcuts, not navigation — render
// them as a compact, on-brand chip button instead of a plain underlined link.
// A subtle translucent background (not a loud fill) keeps it readable even
// though the bubble's link CSS colours the text brand-blue, and sits well with
// the rest of the note UI. `not-prose` opts the anchor out of prose styling.
const PANEL_BTN_CLASS =
  'not-prose inline-flex items-center gap-1 px-2.5 py-1 my-1 rounded-md bg-n-alpha-2 text-n-brand text-sm font-medium no-underline hover:bg-n-alpha-3 cursor-pointer';

const decoratePanelLinks = html =>
  html.replace(/<a\b[^>]*href="#cw-panel\/[^"]*"[^>]*>/g, anchor =>
    anchor.includes('class="')
      ? anchor.replace(/class="[^"]*"/, `class="${PANEL_BTN_CLASS}"`)
      : anchor.replace('<a ', `<a class="${PANEL_BTN_CLASS}" `)
  );

const formattedContent = computed(() => {
  if (variant.value === MESSAGE_VARIANTS.ACTIVITY) {
    return props.content;
  }

  return decoratePanelLinks(
    new MessageFormatter(props.content).formattedMessage
  );
});

// Links written as `[label](#cw-panel/<section>)` (e.g. by the zoho-bridge in
// its private notes) don't navigate — they reveal the matching sidebar
// accordion. Intercept the click here and emit; the conversation view handles
// the rest. Any other link is left untouched.
const PANEL_LINK_PREFIX = '#cw-panel/';
const handleClick = event => {
  const anchor = event.target.closest('a');
  if (!anchor) return;
  const href = anchor.getAttribute('href') || '';
  if (!href.startsWith(PANEL_LINK_PREFIX)) return;
  event.preventDefault();
  emitter.emit(
    BUS_EVENTS.OPEN_CONTACT_SIDEBAR_PANEL,
    href.slice(PANEL_LINK_PREFIX.length)
  );
};
</script>

<template>
  <span
    v-dompurify-html="formattedContent"
    class="prose prose-bubble"
    @click="handleClick"
  />
</template>

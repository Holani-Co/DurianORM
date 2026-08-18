import ApiClient from './ApiClient';

// Durian — saved (unsent) email drafts from the compose flow. Team-shared,
// account-scoped CRUD. Backs the left-nav "Drafts" folder.
class MailDraftsAPI extends ApiClient {
  constructor() {
    super('mail_drafts', { accountScoped: true });
  }
}

export default new MailDraftsAPI();

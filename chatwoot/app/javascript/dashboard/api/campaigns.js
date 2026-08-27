/* global axios */

import ApiClient from './ApiClient';

class CampaignsAPI extends ApiClient {
  constructor() {
    super('campaigns', { accountScoped: true });
  }

  previewAudience(data) {
    return axios.post(`${this.url}/preview_audience`, data);
  }

  pause(id) {
    return axios.post(`${this.url}/${id}/pause`);
  }

  resume(id) {
    return axios.post(`${this.url}/${id}/resume`);
  }

  cancel(id) {
    return axios.post(`${this.url}/${id}/cancel`);
  }

  deliveries(id) {
    return axios.get(`${this.url}/${id}/deliveries`);
  }
}

export default new CampaignsAPI();

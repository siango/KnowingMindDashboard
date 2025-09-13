window.KMS = Object.freeze({
  VERSION: '20250914-030015',
  DATA_MODE: 'auto', // auto|gas|api|mock
  GAS_URL: '',
  API_KEY: '',
  SERVICES: {
    arunroo:   { name:'arunroo-ics',        url:'https://arunroo-ics-asia-southeast1.a.run.app/api/ping' },
    satishift: { name:'satishift-webhook',  url:'https://satishift-webhook-asia-southeast1.a.run.app/api/ping' },
    kma:       { name:'kma-api',            url:'https://kma-api-asia-southeast1.a.run.app/api/ping' }
  }
});
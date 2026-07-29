# Causa remediation draft

## Root cause
real PR integration test from Causa

- service: `payment`
- confidence: 0.900
- score: 0.900

## Fix target `demo-bugs/payment/charge.js`
fix TypeError on amount.nano — guard optional nano

```diff
--- a/demo-bugs/payment/charge.js
+++ b/demo-bugs/payment/charge.js
@@ suggested fix for payment: fix TypeError on amount.nano — guard optional nano @@
# Evidence: real PR integration test from Causa
# Current source excerpt (/home/bhuvi/PROJECTS/causa/demo-bugs/payment/charge.js):
 // Copyright The OpenTelemetry Authors
 // SPDX-License-Identifier: Apache-2.0
 const { context, propagation, trace, metrics, SpanStatusCode } = require('@opentelemetry/api');
 const cardValidator = require('simple-card-validator');
 const { v4: uuidv4 } = require('uuid');
 
 const { OpenFeature } = require('@openfeature/server-sdk');
 const { FlagdProvider } = require('@openfeature/flagd-provider');
 const flagProvider = new FlagdProvider();
 
 const logger = require('./logger');
 const tracer = trace.getTracer('payment');
 const meter = metrics.getMeter('payment');
 const transactionsCounter = meter.createCounter('demo.payment.transactions');
 
 const LOYALTY_LEVEL = ['platinum', 'gold', 'silver', 'bronze'];
 
 /** Return random element from given array */
 function random(arr) {
   const index = Math.floor(Math.random() * arr.length);
   return arr[index];
 }
 
 module.exports.charge = async request => {
   const span = tracer.startSpan('charge');
 
   try {
     const baggage = propagation.getBaggage(context.active());
     const syntheticRequest = baggage?.getEntry('synthetic_request')?.value === 'true';
 
     if (syntheticRequest) {
       span.setAttribute('user_agent.synthetic.type', 'test');
     }
 
     await OpenFeature.setProviderAndWait(flagProvider);
 
     const numberVariant = await OpenFeature.getClient().getNumberValue("paymentFailure", 0);
 
     if (numberVariant > 0) {
       // n% chance to fail with demo.user_context.loyalty_level=gold
```

Draft PR only — never auto-merged. CD: GitHub Actions → Argo canary.



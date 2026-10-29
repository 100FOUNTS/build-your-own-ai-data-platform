# Alarms, thresholds and actions

## Sensor thresholds
| Variable | Alarm | Trip |
|---|---|---|
| Overall vibration (RMS) | 4.5 mm/s | 7.1 mm/s |
| Bearing temperature | 85 °C | 95 °C |
| Motor current | 110 % of rated | 125 % of rated |
| Discharge pressure | drop of 10 % below rated | drop of 25 % |

## Risk computed by the model
Every day the predictive maintenance system delivers a failure risk between 0 and 1 for each pump, which estimates the probability of failure in the next 7 days.
- Risk between 0.5 and 0.8: schedule an inspection within 48 hours at most.
- Risk of 0.8 or more: schedule a planned shutdown within 24 hours at most and prepare spare parts.
- Risk below 0.5: follow the normal preventive schedule.

## Who acts
The operator acknowledges the alert in the system. The reliability engineer decides whether to create a work order. Criticality A pumps have priority in any work queue.

## False alerts
An alert that is not confirmed in the inspection is closed with the cause that was found; that record is later used to improve the model.

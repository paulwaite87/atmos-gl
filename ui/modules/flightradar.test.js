// Tests for flightradar.js's pure dead-reckoning + freeze-clamp helpers (issue #203,
// docs/adr/0009). Expected values are worked by hand from first principles (60 knots
// for 1 hour = 60 nautical miles = exactly 1 degree of latitude), not recomputed the
// way the code does, so a broken formula can actually disagree with the test.
import { describe, test, expect } from 'vitest';
import { interpolatedPosition, boundedElapsedSeconds, isFrozen, flightStatus, targetAltitudeLabel, aircraftClass } from './flightradar.js';

describe('interpolatedPosition', () => {
    test('due-north flight for 1 hour at 60kts moves exactly 1 degree of latitude', () => {
        const pos = interpolatedPosition({ lat: 10, lon: 20, gs: 60, track: 0 }, 3600);
        expect(pos.lat).toBeCloseTo(11.0, 6);
        expect(pos.lon).toBeCloseTo(20.0, 6);
    });

    test('due-south flight moves latitude negative', () => {
        const pos = interpolatedPosition({ lat: 10, lon: 20, gs: 60, track: 180 }, 3600);
        expect(pos.lat).toBeCloseTo(9.0, 6);
        expect(pos.lon).toBeCloseTo(20.0, 6);
    });

    test('due-east flight at the equator moves exactly 1 degree of longitude', () => {
        const pos = interpolatedPosition({ lat: 0, lon: 20, gs: 60, track: 90 }, 3600);
        expect(pos.lat).toBeCloseTo(0.0, 6);
        expect(pos.lon).toBeCloseTo(21.0, 6);
    });

    test('due-east flight at 60deg latitude moves 2 degrees of longitude (convergence)', () => {
        const pos = interpolatedPosition({ lat: 60, lon: 20, gs: 60, track: 90 }, 3600);
        expect(pos.lat).toBeCloseTo(60.0, 6);
        expect(pos.lon).toBeCloseTo(22.0, 6);
    });

    test('zero elapsed time means no movement', () => {
        const pos = interpolatedPosition({ lat: 10, lon: 20, gs: 400, track: 45 }, 0);
        expect(pos).toEqual({ lat: 10, lon: 20 });
    });

    test('zero ground speed means no movement', () => {
        const pos = interpolatedPosition({ lat: 10, lon: 20, gs: 0, track: 45 }, 100);
        expect(pos).toEqual({ lat: 10, lon: 20 });
    });

    test('missing track (no heading data) means no movement', () => {
        const pos = interpolatedPosition({ lat: 10, lon: 20, gs: 400, track: undefined }, 100);
        expect(pos).toEqual({ lat: 10, lon: 20 });
    });
});

describe('boundedElapsedSeconds', () => {
    test('returns real elapsed seconds when under the cap', () => {
        expect(boundedElapsedSeconds(0, 3000, 10)).toBeCloseTo(3.0, 6);
    });

    test('clamps to the cap once elapsed time exceeds it', () => {
        expect(boundedElapsedSeconds(0, 60000, 10)).toBe(10);
    });

    test('never returns negative (a lastSeen in the future, e.g. clock skew)', () => {
        expect(boundedElapsedSeconds(5000, 0, 10)).toBe(0);
    });

    test('defaults the cap to MAX_EXTRAPOLATION_S when omitted', () => {
        expect(boundedElapsedSeconds(0, 3000)).toBeCloseTo(3.0, 6);
    });
});

describe('isFrozen', () => {
    test('not frozen while under the cap', () => {
        expect(isFrozen(0, 3000, 10)).toBe(false);
    });

    test('frozen once elapsed time reaches the cap', () => {
        expect(isFrozen(0, 10000, 10)).toBe(true);
    });

    test('frozen once elapsed time exceeds the cap', () => {
        expect(isFrozen(0, 60000, 10)).toBe(true);
    });

    test('defaults the cap to MAX_EXTRAPOLATION_S when omitted', () => {
        expect(isFrozen(0, 3000)).toBe(false);
        expect(isFrozen(0, 60000)).toBe(true);
    });
});

describe('flightStatus', () => {
    test('a clearly positive climb rate is Climbing', () => {
        expect(flightStatus(1000)).toBe('Climbing');
    });

    test('a clearly negative rate is Descending', () => {
        expect(flightStatus(-1000)).toBe('Descending');
    });

    test('zero rate is Level flight', () => {
        expect(flightStatus(0)).toBe('Level flight');
    });

    test('small positive noise within the deadband stays Level flight', () => {
        expect(flightStatus(100, 150)).toBe('Level flight');
    });

    test('small negative noise within the deadband stays Level flight', () => {
        expect(flightStatus(-100, 150)).toBe('Level flight');
    });

    test('just past the deadband on either side switches state', () => {
        expect(flightStatus(151, 150)).toBe('Climbing');
        expect(flightStatus(-151, 150)).toBe('Descending');
    });

    test('missing rate data defaults to Level flight', () => {
        expect(flightStatus(null)).toBe('Level flight');
        expect(flightStatus(undefined)).toBe('Level flight');
    });
});

describe('targetAltitudeLabel', () => {
    test('an exact match reads as Reached', () => {
        expect(targetAltitudeLabel(37000, 37000)).toBe('Reached');
    });

    test('within tolerance (real-world sensor/MCP noise) also reads as Reached', () => {
        // A real adsb.lol record at cruise: alt_baro=37000, nav_altitude_mcp=36992.
        expect(targetAltitudeLabel(36992, 37000)).toBe('Reached');
    });

    test('a target well away from current altitude renders the formatted number', () => {
        expect(targetAltitudeLabel(38000, 35000)).toBe('38,000 ft');
    });

    test('no target altitude data available renders nothing', () => {
        expect(targetAltitudeLabel(null, 35000)).toBe(null);
        expect(targetAltitudeLabel(undefined, 35000)).toBe(null);
    });

    test('current altitude unknown still shows the raw target', () => {
        expect(targetAltitudeLabel(37000, null)).toBe('37,000 ft');
    });
});

describe('aircraftClass', () => {
    test('a real live-captured widebody type designator resolves correctly', () => {
        // B77W = Boeing 777-300ER, captured from real adsb.lol traffic (see #203's popup work).
        expect(aircraftClass('B77W')).toBe('Widebody Jet');
    });

    test('resolves one designator from each register category', () => {
        expect(aircraftClass('B738')).toBe('Narrowbody Jet');   // 737-800
        expect(aircraftClass('E190')).toBe('Regional Jet');     // Embraer E190
        expect(aircraftClass('AT76')).toBe('Turboprop');        // ATR72-600
        expect(aircraftClass('GLF6')).toBe('Business Jet');     // Gulfstream G650
        expect(aircraftClass('C172')).toBe('Light Aircraft');   // Cessna 172
        expect(aircraftClass('R44')).toBe('Helicopter');        // Robinson R44
        expect(aircraftClass('F16')).toBe('Military Aircraft'); // F-16
    });

    test('is case-insensitive (adsb.lol always sends uppercase, but do not depend on it)', () => {
        expect(aircraftClass('b77w')).toBe('Widebody Jet');
    });

    test('an unregistered designator falls back to a vague default', () => {
        expect(aircraftClass('ZZZZ')).toBe('Aircraft (unclassified)');
    });

    test('missing type data falls back to the same vague default', () => {
        expect(aircraftClass(null)).toBe('Aircraft (unclassified)');
        expect(aircraftClass(undefined)).toBe('Aircraft (unclassified)');
        expect(aircraftClass('')).toBe('Aircraft (unclassified)');
    });
});

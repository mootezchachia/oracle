import { useRef, useEffect } from "react";
import { createChart, AreaSeries } from "lightweight-charts";

export default function PriceChart({ history, oracleValue, label }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const areaSeriesRef = useRef(null);
  const oracleLineRef = useRef(null);

  // Create chart once
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: "#0a0e13" },
        textColor: "#5a6a7e",
        fontFamily: "JetBrains Mono, monospace",
      },
      grid: {
        vertLines: { color: "#1e2a3a" },
        horzLines: { color: "#1e2a3a" },
      },
      timeScale: {
        borderColor: "#1e2a3a",
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: "#1e2a3a",
      },
      crosshair: {
        horzLine: { color: "#5a6a7e", style: 3 },
        vertLine: { color: "#5a6a7e", style: 3 },
      },
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight || 300,
    });

    const areaSeries = chart.addSeries(AreaSeries, {
      lineColor: "#3fb950",
      topColor: "rgba(63, 185, 80, 0.28)",
      bottomColor: "rgba(63, 185, 80, 0.02)",
      lineWidth: 2,
      priceLineVisible: false,
    });

    chartRef.current = chart;
    areaSeriesRef.current = areaSeries;

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight || 300,
        });
      }
    };

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
      areaSeriesRef.current = null;
      oracleLineRef.current = null;
    };
  }, []);

  // Update data when history changes
  useEffect(() => {
    if (!areaSeriesRef.current || !history || history.length === 0) return;

    const data = history
      .map((d) => ({
        time: d.ts,
        value: d.price,
      }))
      .sort((a, b) => a.time - b.time);

    areaSeriesRef.current.setData(data);

    // Remove old oracle line if exists
    if (oracleLineRef.current) {
      areaSeriesRef.current.removePriceLine(oracleLineRef.current);
      oracleLineRef.current = null;
    }

    // Add oracle estimate line
    if (oracleValue != null) {
      oracleLineRef.current = areaSeriesRef.current.createPriceLine({
        price: oracleValue,
        color: "#f0b429",
        lineWidth: 2,
        lineStyle: 1, // dashed
        axisLabelVisible: true,
        title: label ? `Oracle: ${label}` : "Oracle Estimate",
      });
    }

    if (chartRef.current) {
      chartRef.current.timeScale().fitContent();
    }
  }, [history, oracleValue, label]);

  return (
    <div
      ref={containerRef}
      className="w-full h-[300px] rounded-lg overflow-hidden"
    />
  );
}

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import seaborn as sns
from scipy import stats


plt.rcParams["font.family"] = "serif"
plt.rcParams["font.size"] = 10
plt.rcParams["axes.linewidth"] = 0.8
plt.rcParams['figure.dpi'] = 300


COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c"]
EDGE_COLORS = ["#0d47a1", "#e65100", "#1b5e20"]
LIGHT_COLORS = ["#bbdefb", "#ffe0b2", "#c8e6c9"]


buckets = ['B0', 'B1', 'B2']
simple_sr = [88.64, 89.04, 93.65]
medium_sr = [81.48, 67.82, 79.10]
hard_sr = [81.50, 68.65, 73.10]
overall_sr = [99.80, 99.32, 98.67]


simple_cr = [99.94, 99.34, 99.25]
medium_cr = [99.85, 99.04, 98.75]
hard_cr = [99.80, 99.60, 98.00]


simple_f1 = [21.1, 28.3, np.nan]
medium_f1 = [16.6, 31.5, 56.2]
hard_f1 = [15.1, 29.1, 48.5]


def _save(fig, name):
    fig.tight_layout()
    fig.savefig(f"{name}.pdf", bbox_inches="tight", dpi=300)
    fig.savefig(f"{name}.png", dpi=300, bbox_inches="tight")


def plot_radar_comparison():
    from math import pi
    from matplotlib import rcParams

    rcParams['font.family'] = 'Times New Roman'

    fig, axes = plt.subplots(1, 3, figsize=(15, 5), subplot_kw=dict(projection='polar'))

    categories = ['Simple SR', 'Medium SR', 'Hard SR', 'Avg CR', 'Avg SC']

    for idx, (ax, bucket) in enumerate(zip(axes, buckets)):
        values = [
            simple_sr[idx],
            medium_sr[idx],
            hard_sr[idx],
            np.mean([simple_cr[idx], medium_cr[idx], hard_cr[idx]]),
            np.nanmean([simple_f1[idx], medium_f1[idx], hard_f1[idx]]) if not np.isnan(
                [simple_f1[idx], medium_f1[idx], hard_f1[idx]]).all() else 0
        ]

        N = len(categories)
        angles = [n / float(N) * 2 * pi for n in range(N)]
        angles += angles[:1]
        values += values[:1]

        ax.plot(angles, values, 'o-', linewidth=2, label=bucket, color=COLORS[idx])
        ax.fill(angles, values, alpha=0.25, color=COLORS[idx])

        ax.set_xticks(angles[:-1])
        labels = ax.set_xticklabels(categories, fontsize=10)

        for i, label in enumerate(labels):
            if categories[i] in ['Simple SR', 'Avg CR']:
                label.set_color('#FFFFFF')
            else:
                label.set_color('#000000')

        ax.set_ylim(0, 100)
        ax.set_title(f'Bucket {bucket}', fontsize=12, fontweight='bold', pad=20)
        ax.grid(True)

        for dim_i, (angle, value) in enumerate(zip(angles[:-1], values[:-1])):
            if dim_i == 0:
                ax.text(angle, value + 5, f'{value:.2f}', ha='center', va='center',
                        fontsize=8, fontweight='bold', alpha=0.0)
            else:
                ax.text(angle, value + 5, f'{value:.2f}', ha='center', va='center',
                        fontsize=8, fontweight='bold', color='#000000', alpha=1.0)

    _save(fig, "fig_3")



if __name__ == "__main__":
    plot_radar_comparison()


    print("Done! Generated 5 improved research plots:")
    print("- fig_3.pdf/png")

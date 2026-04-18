# My ROS2 Project

A ROS2 Humble workspace using [pixi](https://pixi.sh) for reproducible environment management.

## Prerequisites

- **Windows 10/11** (this project is configured for win-64)
- No ROS2 installation required - pixi handles everything!

## Quick Start

### 1. Install pixi

Open PowerShell and run:
```powershell
iwr -useb https://pixi.sh/install.ps1 | iex
```

Restart your terminal after installation.

### 2. Clone and Setup

```powershell
git clone <your-repo-url>
cd my_ros2_project
pixi install
```

This downloads all ROS2 dependencies (~570 packages). First run takes a few minutes.

### 3. Build the Workspace

```powershell
pixi run build
```

### 4. Run Commands

| Command | Description |
|---------|-------------|
| `pixi run sim` | Launch turtlesim simulation |
| `pixi run hello` | Run the custom node (auto-builds first) |
| `pixi run build` | Rebuild packages |

## Project Structure

```
my_ros2_project/
├── pixi.toml        # Project config & dependencies
├── pixi.lock        # Locked dependency versions
├── src/
│   └── my_package/  # Custom ROS2 Python package
│       ├── my_package/
│       │   ├── __init__.py
│       │   └── my_node.py    # Example node
│       ├── package.xml
│       ├── setup.py
│       └── setup.cfg
├── install/         # Built packages (generated)
├── build/           # Build artifacts (generated)
└── log/             # Build logs (generated)
```

## Adding New Packages

Create packages in the `src/` directory, then rebuild:
```powershell
pixi run build
```

## Adding Dependencies

```powershell
pixi add ros-humble-<package-name>
```

Browse available packages at [robostack](https://robostack.github.io/).

## Troubleshooting

### "failed to create process" errors
Some ROS2 commands have symlink issues on Windows. Use the Python module workaround:
```powershell
pixi run python -m colcon build
```

### pixi shell fails
This can happen due to spaces in Windows PATH. Use `pixi run <command>` instead.

## Resources

- [Pixi ROS2 Tutorial](https://pixi.prefix.dev/latest/tutorials/ros2/)
- [ROS2 Humble Documentation](https://docs.ros.org/en/humble/)
- [Robostack](https://robostack.github.io/) - ROS packages for conda/pixi
